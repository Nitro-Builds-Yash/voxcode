import * as vscode from 'vscode';

// ---------------------------------------------------------------- types
interface FileChange {
    path: string;
    original: string;
    proposed: string;
    status: string;
}

interface PendingEdit {
    summary: string;
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
type ApplyTarget = 'working_tree' | 'branch' | 'pull_request';

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
        reviewPlans: c.get<boolean>('reviewPlans') ?? false,
        applyTarget: (c.get<string>('applyTarget') || 'working_tree') as ApplyTarget,
        showTimings: c.get<boolean>('showTimings') ?? true,
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

/** Open an SSE endpoint (POST with a body, or GET) and call onEvent for each `data:` JSON payload. */
async function stream(
    path: string,
    body: unknown,
    onEvent: (e: StreamEvent) => void,
    signal?: AbortSignal
): Promise<void> {
    const { backendUrl, apiToken } = config();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiToken) headers['X-VoxCode-Token'] = apiToken;
    const response = await fetch(`${backendUrl}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
    });
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

function speak(text: string, replyId?: string) {
    if (!config().speakReplies || !text.trim()) return;
    api('/voice/speak', { text, play: true, reply_id: replyId }).catch(() => undefined);
}

function stopSpeaking() {
    if (config().speakReplies) api('/voice/stop-speaking', {}).catch(() => undefined);
}

const STAGE_LABELS: Record<string, string> = {
    stt: 'speech',
    query_rewrite: 'rewrite',
    retrieval: 'search',
    rerank: 'rerank',
    context: 'context',
    first_token: 'first token',
    generation: 'answer',
    edit_plan: 'plan',
    edit_code: 'patch',
    edit_tests: 'tests',
    edit_review: 'review',
};

function showTimings(timings?: Record<string, number>, usage?: Record<string, any>) {
    if (!timings || !config().showTimings) return;
    const parts = Object.entries(STAGE_LABELS)
        .filter(([key]) => timings[key] !== undefined)
        .map(([key, label]) => `${label} ${Math.round(timings[key])} ms`);
    if (usage && (usage.input_tokens || usage.output_tokens)) {
        let tokens = `${usage.input_tokens + (usage.cache_read_tokens || 0)} in / ${usage.output_tokens} out tokens`;
        if (usage.cache_read_tokens) tokens += ` (${usage.cache_read_tokens} cached)`;
        if (usage.estimated_cost_usd) tokens += ` · $${usage.estimated_cost_usd.toFixed(4)}`;
        parts.push(tokens);
    }
    if (parts.length) {
        output.appendLine(`  ⏱ ${parts.join(' · ')}`);
        vscode.window.setStatusBarMessage(`VoxCode: ${parts.slice(0, 3).join(' · ')}`, 8000);
    }
}

// ---------------------------------------------------------------- voice
async function toggleRecording() {
    if (busy) return;
    if (!recording) {
        stopSpeaking(); // barge-in
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
async function runAgent(query: string, extra: Record<string, unknown> = {}) {
    const repo = workspaceRoot();
    if (!repo) {
        vscode.window.showWarningMessage('Open a folder first so VoxCode knows which repository to use.');
        return;
    }
    busy = true;
    setStatus('$(sync~spin) Thinking…');
    output.show(true);
    const replyId = `r${Date.now()}`;
    let sentence = '';
    let gotTokens = false;
    let planned: StreamEvent | undefined;
    try {
        await stream(
            '/agent/stream',
            {
                query,
                repository_path: repo,
                mode: 'auto',
                run_tests: config().runTestsOnEdit,
                plan_only: config().reviewPlans && extra.plan === undefined,
                ...extra,
            },
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
                            speak(match[1], replyId);
                            sentence = match[2];
                        }
                        break;
                    }
                    case 'done':
                        if (gotTokens) {
                            output.appendLine('');
                            speak(sentence, replyId);
                        } else if (event.answer) {
                            // Agentic mode returns the answer in one piece.
                            output.appendLine(event.answer);
                            speak(event.answer, replyId);
                        }
                        showTimings(event.timings_ms, event.usage);
                        break;
                    case 'edit':
                        if (event.status === 'planned') planned = event;
                        else void receiveEdit(repo, event);
                        break;
                    case 'timings':
                        showTimings(event.timings_ms);
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
    if (planned) await reviewPlan(query, planned);
}

/** Show the drafted plan; on approval, run the edit with it (optionally edited). */
async function reviewPlan(query: string, planned: StreamEvent) {
    const plan: string = planned.plan || planned.summary || '';
    output.appendLine(`\nPlan:\n${plan}\n`);
    speak('Here is my plan. Approve it, edit it, or cancel.');
    const choice = await vscode.window.showInformationMessage(
        'VoxCode drafted a plan (see the VoxCode panel).',
        { modal: true, detail: plan },
        'Approve',
        'Edit plan'
    );
    if (choice === 'Approve') {
        await runAgent(query, { mode: 'edit', plan });
    } else if (choice === 'Edit plan') {
        const doc = await vscode.workspace.openTextDocument({ content: plan, language: 'markdown' });
        await vscode.window.showTextDocument(doc);
        const go = await vscode.window.showInformationMessage('Edit the plan, then run it.', 'Run edited plan');
        if (go) await runAgent(query, { mode: 'edit', plan: doc.getText() });
    }
}

// ---------------------------------------------------------------- hands-free
let listenAbort: AbortController | undefined;

async function toggleHandsFree() {
    if (listenAbort) {
        listenAbort.abort();
        listenAbort = undefined;
        await api('/voice/listen/stop', {}).catch(() => undefined);
        idle();
        vscode.window.setStatusBarMessage('VoxCode: hands-free off', 3000);
        return;
    }
    listenAbort = new AbortController();
    const signal = listenAbort.signal;
    try {
        await api('/voice/listen/start', {});
    } catch (err: any) {
        listenAbort = undefined;
        vscode.window.showErrorMessage(`Hands-free mode unavailable: ${err.message}`);
        return;
    }
    setStatus('$(radio-tower) Vox listening', 'Hands-free: just talk. Click to stop.');
    stream(
        '/voice/listen/events',
        undefined,
        (event) => {
            switch (event.type) {
                case 'wake':
                    setStatus('$(megaphone) Yes?');
                    break;
                case 'speech_start':
                    stopSpeaking();
                    setStatus('$(record) Hearing you…');
                    break;
                case 'partial':
                    setStatus(`$(record) ${String(event.text).slice(-40) || 'Hearing you…'}`);
                    break;
                case 'final':
                    setStatus('$(radio-tower) Vox listening', 'Hands-free: just talk. Click to stop.');
                    showTimings(event.timings_ms);
                    void handleUtterance(event.transcription, event.command).then(() => {
                        if (listenAbort) setStatus('$(radio-tower) Vox listening', 'Hands-free: just talk. Click to stop.');
                    });
                    break;
                case 'error':
                    vscode.window.showWarningMessage(`VoxCode: ${event.detail}`);
                    break;
            }
        },
        signal
    ).catch((err) => {
        if (!signal.aborted) vscode.window.showErrorMessage(`VoxCode listening stopped: ${err.message}`);
        listenAbort = undefined;
        idle();
    });
}

// ---------------------------------------------------------------- edits
async function receiveEdit(repo: string, result: StreamEvent) {
    output.appendLine(`[${result.status}] ${result.summary}`);
    if (!result.diff) {
        vscode.window.showWarningMessage(`VoxCode could not produce a working patch: ${result.summary}`);
        return;
    }
    pending = { repo, diff: result.diff, files: result.files, index: 0, status: result.status, summary: result.summary || '' };
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
        'Open PR',
        'Reject',
        ...(result.files.length > 1 ? ['Next file'] : [])
    );
    if (choice === 'Accept') await acceptEdit();
    else if (choice === 'Open PR') await acceptEdit('pull_request');
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

async function acceptEdit(target?: ApplyTarget) {
    if (!pending) {
        vscode.window.setStatusBarMessage('VoxCode: no pending patch', 3000);
        return;
    }
    const edit = pending;
    target = target ?? config().applyTarget;
    let result: any;
    try {
        result = await api('/agent/apply', {
            repository_path: edit.repo,
            diff: edit.diff,
            target,
            title: edit.summary.split('\n')[0].slice(0, 120) || 'VoxCode change',
            description: `${edit.summary}\n\nRequested by voice with VoxCode.`,
        });
    } catch (err: any) {
        vscode.window.showErrorMessage(`Apply failed: ${err.message}`);
        idle();
        return;
    }
    pending = undefined;
    previews.clear();
    idle();
    if (target !== 'working_tree') {
        const where = result.pull_request_url ? `opened ${result.pull_request_url}` : `committed on branch ${result.branch}`;
        const open = result.pull_request_url ? await vscode.window.showInformationMessage(`VoxCode ${where}.`, 'Open PR') : undefined;
        if (!result.pull_request_url) vscode.window.showInformationMessage(`VoxCode ${where}; your working files are unchanged.`);
        if (open) await vscode.env.openExternal(vscode.Uri.parse(result.pull_request_url));
        return;
    }
    const applied: string[] = result.files_modified;
    vscode.window.showInformationMessage(`VoxCode applied changes to ${applied.join(', ')}. Say "undo" to revert.`);
    const opened = edit.files.find((f) => f.status !== 'deleted');
    if (opened) {
        await vscode.window.showTextDocument(vscode.Uri.file(`${edit.repo}/${opened.path}`)).then(
            undefined,
            () => undefined
        );
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
async function indexWorkspace(full = false, quiet = false) {
    const repo = workspaceRoot();
    if (!repo) {
        if (!quiet) vscode.window.showWarningMessage('No workspace open to index.');
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
        if (quiet) {
            // Server not started yet: stay out of the way on VS Code startup.
            output.appendLine(`Startup indexing skipped: ${err.message}`);
        } else {
            vscode.window.showErrorMessage(`Indexing failed: ${err.message}`);
        }
    } finally {
        idle();
    }
}

// ---------------------------------------------------------------- @vox chat participant
const chatHandler: vscode.ChatRequestHandler = async (request, _context, response, token) => {
    const repo = workspaceRoot();
    if (!repo) {
        response.markdown('Open a folder first so I know which repository to use.');
        return {};
    }
    const mode = request.command === 'edit' ? 'edit' : request.command === 'agent' ? 'agentic' : 'auto';
    const abort = new AbortController();
    token.onCancellationRequested(() => abort.abort());
    response.progress(mode === 'edit' ? 'Writing and validating a patch…' : 'Searching the codebase…');
    try {
        await stream(
            '/agent/stream',
            { query: request.prompt, repository_path: repo, mode, session_id: 'vscode-chat', run_tests: config().runTestsOnEdit },
            (event) => {
                switch (event.type) {
                    case 'sources':
                        for (const source of (event.sources || []).slice(0, 6)) {
                            const uri = vscode.Uri.file(`${repo}/${source.file_path}`);
                            response.reference(
                                new vscode.Location(uri, new vscode.Range(source.start_line - 1, 0, source.end_line - 1, 0))
                            );
                        }
                        break;
                    case 'tool':
                        response.progress(`${event.tool}: ${JSON.stringify(event.args).slice(0, 80)}`);
                        break;
                    case 'token':
                        response.markdown(event.text);
                        break;
                    case 'done':
                        if (event.answer && event.tool_mode !== undefined) response.markdown(event.answer);
                        break;
                    case 'edit':
                        renderEditInChat(repo, event, response);
                        break;
                    case 'error':
                        response.markdown(`\n\n**Error:** ${event.detail}`);
                        break;
                }
            },
            abort.signal
        );
    } catch (err: any) {
        if (!abort.signal.aborted) response.markdown(`**VoxCode is not reachable:** ${err.message}`);
    }
    return {};
};

function renderEditInChat(repo: string, event: StreamEvent, response: vscode.ChatResponseStream) {
    if (event.plan) response.markdown(`**Plan**\n\n${event.plan}\n\n`);
    response.markdown(`**${event.status}**: ${event.summary}\n\n`);
    if (!event.diff) return;
    response.markdown('```diff\n' + event.diff + '\n```\n');
    pending = { repo, diff: event.diff, files: event.files, index: 0, status: event.status, summary: event.summary || '' };
    idle();
    response.button({ command: 'voiceCoding.acceptPatch', title: 'Accept' });
    response.button({ command: 'voiceCoding.reviewPatch', title: 'Review diff' });
    response.button({ command: 'voiceCoding.acceptAsPullRequest', title: 'Open PR' });
    response.button({ command: 'voiceCoding.rejectPatch', title: 'Reject' });
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
        vscode.commands.registerCommand('voiceCoding.acceptPatch', () => acceptEdit()),
        vscode.commands.registerCommand('voiceCoding.acceptAsPullRequest', () => acceptEdit('pull_request')),
        vscode.commands.registerCommand('voiceCoding.acceptAsBranch', () => acceptEdit('branch')),
        vscode.commands.registerCommand('voiceCoding.reviewPatch', () => showPendingFile(0)),
        vscode.commands.registerCommand('voiceCoding.toggleHandsFree', toggleHandsFree),
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
    // @vox in VS Code's Chat view (VS Code 1.90+).
    if (vscode.chat?.createChatParticipant) {
        const participant = vscode.chat.createChatParticipant('voxcode.vox', chatHandler);
        participant.iconPath = new vscode.ThemeIcon('mic');
        context.subscriptions.push(participant);
    }

    if (workspaceRoot()) void indexWorkspace(false, true);
}

export function deactivate() {
    if (recording) void api('/voice/record/stop', {}).catch(() => undefined);
    if (listenAbort) {
        listenAbort.abort();
        void api('/voice/listen/stop', {}).catch(() => undefined);
    }
}
