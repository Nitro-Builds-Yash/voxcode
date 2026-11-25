import * as vscode from 'vscode';

// ---------------------------------------------------------------- types
interface FileChange {
    path: string;
    original: string;
    proposed: string;
    status: string;
}

interface PendingEdit {
    repo: string;
    diff: string;
    files: FileChange[];
    index: number;
    status: string;
}

interface VoiceResult {
    transcription: string;
    command: string | null;
}

type StreamEvent = { type: string; [key: string]: any };

// ---------------------------------------------------------------- state
let statusBar: vscode.StatusBarItem;
let output: vscode.OutputChannel;
let recording = false;
let busy = false;
let pending: PendingEdit | undefined;
const previews = new Map<string, string>();
const PREVIEW_SCHEME = 'voxcode-preview';

// ---------------------------------------------------------------- config
function config() {
    const c = vscode.workspace.getConfiguration('voiceCoding');
    return {
        backendUrl: (c.get<string>('backendUrl') || 'http://localhost:8000').replace(/\/$/, ''),
        apiToken: c.get<string>('apiToken') || '',
        autoApplyDiffs: c.get<boolean>('autoApplyDiffs') ?? false,
        speakReplies: c.get<boolean>('speakReplies') ?? false,
        runTestsOnEdit: c.get<boolean>('runTestsOnEdit') ?? false,
        watchWorkspace: c.get<boolean>('watchWorkspace') ?? true,
    };
}

function workspaceRoot(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

// ---------------------------------------------------------------- http
class BackendError extends Error {
    constructor(message: string, public status: number) {
        super(message);
    }
}

async function api<T = any>(path: string, body?: unknown, timeoutMs = 300_000): Promise<T> {
    const { backendUrl, apiToken } = config();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiToken) headers['X-VoxCode-Token'] = apiToken;
    let response: Response;
    try {
        response = await fetch(`${backendUrl}${path}`, {
            method: body === undefined ? 'GET' : 'POST',
            headers,
            body: body === undefined ? undefined : JSON.stringify(body),
            signal: AbortSignal.timeout(timeoutMs),
        });
    } catch (err: any) {
        throw new BackendError(`Cannot reach VoxCode at ${backendUrl} (${err.message}). Is the server running?`, 0);
    }
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) throw new BackendError(data.detail || `HTTP ${response.status}`, response.status);
    return data as T;
}

/** POST to an SSE endpoint and call onEvent for each `data:` JSON payload. */
async function stream(path: string, body: unknown, onEvent: (e: StreamEvent) => void): Promise<void> {
    const { backendUrl, apiToken } = config();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiToken) headers['X-VoxCode-Token'] = apiToken;
    const response = await fetch(`${backendUrl}${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
    if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new BackendError((data as any).detail || `HTTP ${response.status}`, response.status);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary: number;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const chunk = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            for (const line of chunk.split('\n')) {
                if (line.startsWith('data:')) onEvent(JSON.parse(line.slice(5)));
            }
        }
    }
}

// ---------------------------------------------------------------- ui helpers
function setStatus(text: string, tooltip?: string) {
    statusBar.text = text;
    statusBar.tooltip = tooltip ?? 'VoxCode: click to talk (Ctrl+Alt+V)';
}

function idle() {
    setStatus(pending ? '$(mic) Vox · $(diff) review' : '$(mic) Vox');
}

function speak(text: string) {
    if (!config().speakReplies || !text.trim()) return;
    api('/voice/speak', { text, play: true }).catch(() => undefined);
}

// ---------------------------------------------------------------- voice
async function toggleRecording() {
    if (busy) return;
    if (!recording) {
        try {
            await api('/voice/record/start', {});
            recording = true;
            setStatus('$(record) Listening… click to stop', 'VoxCode is recording. Click or press Ctrl+Alt+V to stop.');
        } catch (err: any) {
            // No microphone / no Whisper on the backend: fall back to typing.
            vscode.window.showWarningMessage(`Voice unavailable (${err.message}). Type your instruction instead.`);
            await typeInstruction();
        }
        return;
    }
    recording = false;
    setStatus('$(sync~spin) Transcribing…');
    try {
        const result = await api<VoiceResult>('/voice/record/stop', {});
        idle();
        await handleUtterance(result.transcription, result.command);
    } catch (err: any) {
        idle();
        vscode.window.showErrorMessage(`VoxCode: ${err.message}`);
    }
}

async function typeInstruction() {
    const text = await vscode.window.showInputBox({
        prompt: 'Ask VoxCode a question or describe a change',
        placeHolder: 'e.g. "Where is the login flow?" or "Add input validation to create_user"',
    });
    if (text) await handleUtterance(text, null);
}

async function handleUtterance(text: string, command: string | null) {
    if (!text.trim()) {
        vscode.window.setStatusBarMessage('VoxCode: heard nothing', 3000);
        return;
    }
    output.appendLine(`\n> ${text}`);
    if (command) {
        await runVoiceCommand(command);
        return;
    }
    await runAgent(text);
}

async function runVoiceCommand(command: string) {
    switch (command) {
        case 'accept':
            return acceptEdit();
        case 'reject':
            return rejectEdit();
        case 'undo':
            return undoEdit();
        case 'next_file':
            return showPendingFile(1);
        case 'previous_file':
            return showPendingFile(-1);
        case 'run_tests':
            return runTests();
        case 'reindex':
            return indexWorkspace();
        case 'stop':
            return;
    }
}

// ---------------------------------------------------------------- agent
async function runAgent(query: string) {
    const repo = workspaceRoot();
    if (!repo) {
        vscode.window.showWarningMessage('Open a folder first so VoxCode knows which repository to use.');
        return;
    }
    busy = true;
    setStatus('$(sync~spin) Thinking…');
    output.show(true);
    let sentence = '';
    let gotTokens = false;
    try {
        await stream(
            '/agent/stream',
            { query, repository_path: repo, mode: 'auto', run_tests: config().runTestsOnEdit },
            (event) => {
                switch (event.type) {
                    case 'start':
                        if (event.mode === 'edit') setStatus('$(sync~spin) Writing a patch…');
                        break;
                    case 'tool':
                        output.appendLine(`  [${event.tool}] ${JSON.stringify(event.args)}`);
                        break;
                    case 'token': {
                        gotTokens = true;
                        output.append(event.text);
                        sentence += event.text;
                        // Speak sentence by sentence while the rest streams in.
                        const match = sentence.match(/^([\s\S]*?[.!?])\s+([\s\S]*)$/);
                        if (match) {
                            speak(match[1]);
                            sentence = match[2];
                        }
                        break;
                    }
                    case 'done':
                        if (gotTokens) {
                            output.appendLine('');
                            speak(sentence);
                        } else if (event.answer) {
                            // Agentic mode returns the answer in one piece.
                            output.appendLine(event.answer);
                            speak(event.answer);
                        }
                        break;
                    case 'edit':
                        void receiveEdit(repo, event);
                        break;
                    case 'error':
                        throw new BackendError(event.detail, 500);
                }
            }
        );
    } catch (err: any) {
        vscode.window.showErrorMessage(`VoxCode: ${err.message}`);
    } finally {
        busy = false;
        idle();
    }
}

// ---------------------------------------------------------------- edits
async function receiveEdit(repo: string, result: StreamEvent) {
    output.appendLine(`[${result.status}] ${result.summary}`);
    if (!result.diff) {
        vscode.window.showWarningMessage(`VoxCode could not produce a working patch: ${result.summary}`);
        return;
    }
    pending = { repo, diff: result.diff, files: result.files, index: 0, status: result.status };
    output.appendLine(result.diff);
    speak(result.summary);

    if (config().autoApplyDiffs && result.status === 'ready') {
        await acceptEdit();
        return;
    }
    await showPendingFile(0);
    const note = result.status === 'ready' ? 'validated' : 'applies cleanly but was not approved by review';
    const choice = await vscode.window.showInformationMessage(
        `VoxCode patch for ${result.files.length} file(s) (${note}). Say "accept" or "reject".`,
        'Accept',
        'Reject',
        ...(result.files.length > 1 ? ['Next file'] : [])
    );
    if (choice === 'Accept') await acceptEdit();
    else if (choice === 'Reject') await rejectEdit();
    else if (choice === 'Next file') await showPendingFile(1);
    idle();
}

async function showPendingFile(step: number) {
    if (!pending || pending.files.length === 0) {
        vscode.window.setStatusBarMessage('VoxCode: no pending patch', 3000);
        return;
    }
    pending.index = (pending.index + step + pending.files.length) % pending.files.length;
    const file = pending.files[pending.index];
    const stamp = Date.now();
    const left = vscode.Uri.parse(`${PREVIEW_SCHEME}:/original/${file.path}?${stamp}`);
    const right = vscode.Uri.parse(`${PREVIEW_SCHEME}:/proposed/${file.path}?${stamp}`);
    previews.set(left.toString(), file.original);
    previews.set(right.toString(), file.proposed);
    await vscode.commands.executeCommand(
        'vscode.diff',
        left,
        right,
        `VoxCode: ${file.path} (${pending.index + 1}/${pending.files.length})`,
        { preview: true }
    );
}

async function acceptEdit() {
    if (!pending) {
        vscode.window.setStatusBarMessage('VoxCode: no pending patch', 3000);
        return;
    }
    try {
        const result = await api('/agent/apply', { repository_path: pending.repo, diff: pending.diff });
        vscode.window.showInformationMessage(`VoxCode applied changes to ${result.files_modified.join(', ')}. Say "undo" to revert.`);
        const first = vscode.Uri.file(`${pending.repo}/${result.files_modified[0]}`);
        pending = undefined;
        await vscode.window.showTextDocument(first);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Apply failed: ${err.message}`);
    } finally {
        idle();
    }
}

async function rejectEdit() {
    pending = undefined;
    previews.clear();
    vscode.window.setStatusBarMessage('VoxCode: patch discarded', 3000);
    idle();
}

async function undoEdit() {
    const repo = workspaceRoot();
    if (!repo) return;
    try {
        const result = await api('/agent/undo', { repository_path: repo });
        vscode.window.showInformationMessage(`VoxCode reverted ${result.files_modified.join(', ')}.`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Undo failed: ${err.message}`);
    }
}

async function runTests() {
    const repo = workspaceRoot();
    if (!repo) return;
    setStatus('$(beaker~spin) Running tests…');
    try {
        const report = await api('/agent/test', { repository_path: repo }, 600_000);
        output.appendLine(`\n$ ${report.command}\n${report.output}`);
        output.show(true);
        const message = report.passed ? 'Tests passed' : 'Tests failed';
        (report.passed ? vscode.window.showInformationMessage : vscode.window.showErrorMessage)(`VoxCode: ${message}`);
        speak(message);
    } catch (err: any) {
        vscode.window.showErrorMessage(`VoxCode: ${err.message}`);
    } finally {
        idle();
    }
}

// ---------------------------------------------------------------- indexing
async function indexWorkspace(full = false) {
    const repo = workspaceRoot();
    if (!repo) {
        vscode.window.showWarningMessage('No workspace open to index.');
        return;
    }
    setStatus('$(sync~spin) Indexing…');
    try {
        const result = await api('/index/', { repository_path: repo, full, watch: config().watchWorkspace });
        vscode.window.setStatusBarMessage(
            `VoxCode indexed ${result.files_indexed} files · ${result.chunks_created} chunks` +
                (result.semantic_indexed ? ' · semantic' : ' · keyword only') +
                (result.watching ? ' · watching' : ''),
            6000
        );
    } catch (err: any) {
        vscode.window.showErrorMessage(`Indexing failed: ${err.message}`);
    } finally {
        idle();
    }
}

// ---------------------------------------------------------------- lifecycle
export function activate(context: vscode.ExtensionContext) {
    output = vscode.window.createOutputChannel('VoxCode');
    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.command = 'voiceCoding.startRecording';
    idle();
    statusBar.show();

    context.subscriptions.push(
        output,
        statusBar,
        vscode.workspace.registerTextDocumentContentProvider(PREVIEW_SCHEME, {
            provideTextDocumentContent: (uri) => previews.get(uri.toString()) ?? '',
        }),
        vscode.commands.registerCommand('voiceCoding.startRecording', toggleRecording),
        vscode.commands.registerCommand('voiceCoding.stopRecording', async () => {
            if (recording) await toggleRecording();
        }),
        vscode.commands.registerCommand('voiceCoding.typeInstruction', typeInstruction),
        vscode.commands.registerCommand('voiceCoding.acceptPatch', acceptEdit),
        vscode.commands.registerCommand('voiceCoding.rejectPatch', rejectEdit),
        vscode.commands.registerCommand('voiceCoding.undoPatch', undoEdit),
        vscode.commands.registerCommand('voiceCoding.nextFile', () => showPendingFile(1)),
        vscode.commands.registerCommand('voiceCoding.runTests', runTests),
        vscode.commands.registerCommand('voiceCoding.indexWorkspace', () => indexWorkspace(false)),
        vscode.commands.registerCommand('voiceCoding.rebuildIndex', () => indexWorkspace(true)),
        vscode.commands.registerCommand('voiceCoding.openSettings', () =>
            vscode.commands.executeCommand('workbench.action.openSettings', 'voiceCoding')
        )
    );

    // Index (and start watching) the workspace in the background on startup.
    if (workspaceRoot()) void indexWorkspace(false);
}

export function deactivate() {
    if (recording) void api('/voice/record/stop', {}).catch(() => undefined);
}
