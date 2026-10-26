import * as vscode from 'vscode';
import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';

let statusBarItem: vscode.StatusBarItem;
let isRecording = false;

function postJson(urlStr: string, payload: any): Promise<any> {
    return new Promise((resolve, reject) => {
        try {
            const parsedUrl = new URL(urlStr);
            const client = parsedUrl.protocol === 'https:' ? https : http;
            const body = JSON.stringify(payload);

            const req = client.request(
                {
                    hostname: parsedUrl.hostname,
                    port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
                    path: parsedUrl.pathname + parsedUrl.search,
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Content-Length': Buffer.byteLength(body),
                    },
                    timeout: 30000,
                },
                (res) => {
                    let data = '';
                    res.on('data', (chunk) => (data += chunk));
                    res.on('end', () => {
                        try {
                            const parsed = data ? JSON.parse(data) : {};
                            if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                                resolve(parsed);
                            } else {
                                reject(new Error(parsed.detail || `HTTP ${res.statusCode}: ${data}`));
                            }
                        } catch (err) {
                            resolve({ raw: data });
                        }
                    });
                }
            );

            req.on('error', (err) => reject(err));
            req.on('timeout', () => {
                req.destroy();
                reject(new Error('Request to VoxCode backend timed out.'));
            });

            req.write(body);
            req.end();
        } catch (e) {
            reject(e);
        }
    });
}

export function activate(context: vscode.ExtensionContext) {
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'voiceCoding.startRecording';
    statusBarItem.text = '$(mic) Voice Code';
    statusBarItem.tooltip = 'Click to prompt voxcode with voice or text';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Command: Start Prompt / Voice Command
    const startCmd = vscode.commands.registerCommand('voiceCoding.startRecording', async () => {
        const config = vscode.workspace.getConfiguration('voiceCoding');
        const backendUrl = config.get<string>('backendUrl') || 'http://localhost:8000';

        const prompt = await vscode.window.showInputBox({
            prompt: 'Enter voice transcription or instruction for VoxCode Agent',
            placeHolder: 'e.g. "Add docstrings to all methods in parser.py" or "Index workspace"',
        });

        if (!prompt) return;

        statusBarItem.text = '$(sync~spin) Thinking...';
        vscode.window.setStatusBarMessage(`VoxCode: Executing prompt...`, 5000);

        try {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            const rootPath = workspaceFolders && workspaceFolders.length > 0 ? workspaceFolders[0].uri.fsPath : '.';

            const response = await postJson(`${backendUrl}/agent/query`, {
                query: prompt,
                repository_path: rootPath,
            });

            vscode.window.showInformationMessage(`VoxCode: ${response.summary || 'Command executed successfully.'}`);

            // If a diff was returned, offer to apply or view it
            if (response.diff) {
                const doc = await vscode.workspace.openTextDocument({
                    content: response.diff,
                    language: 'diff',
                });
                await vscode.window.showTextDocument(doc, { preview: true });
            }
        } catch (error: any) {
            vscode.window.showErrorMessage(`VoxCode Agent Error: ${error.message || error}`);
        } finally {
            statusBarItem.text = '$(mic) Voice Code';
        }
    });

    // Command: Index Workspace
    const indexCmd = vscode.commands.registerCommand('voiceCoding.indexWorkspace', async () => {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders || workspaceFolders.length === 0) {
            vscode.window.showWarningMessage('No workspace open to index.');
            return;
        }

        const rootPath = workspaceFolders[0].uri.fsPath;
        const config = vscode.workspace.getConfiguration('voiceCoding');
        const backendUrl = config.get<string>('backendUrl') || 'http://localhost:8000';

        statusBarItem.text = '$(sync~spin) Indexing...';
        vscode.window.showInformationMessage(`VoxCode: Indexing workspace at ${rootPath}...`);

        try {
            const result = await postJson(`${backendUrl}/index/`, {
                repository_path: rootPath,
            });

            vscode.window.showInformationMessage(
                `Workspace indexed: ${result.chunks_created} chunks across ${result.files_indexed} files (${result.functions_found} functions, ${result.classes_found} classes).`
            );
        } catch (error: any) {
            vscode.window.showErrorMessage(`Indexing failed: ${error.message || error}`);
        } finally {
            statusBarItem.text = '$(mic) Voice Code';
        }
    });

    // Command: Open Settings
    const settingsCmd = vscode.commands.registerCommand('voiceCoding.openSettings', () => {
        vscode.commands.executeCommand('workbench.action.openSettings', 'voiceCoding');
    });

    context.subscriptions.push(startCmd, indexCmd, settingsCmd);
}

export function deactivate() {
    if (statusBarItem) {
        statusBarItem.dispose();
    }
}
