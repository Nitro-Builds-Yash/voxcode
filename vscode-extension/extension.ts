import * as vscode from 'vscode';
import * as http from 'http';

let statusBarItem: vscode.StatusBarItem;
let isRecording = false;

export function activate(context: vscode.ExtensionContext) {
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'voiceCoding.startRecording';
    statusBarItem.text = '$(mic) Voice Code';
    statusBarItem.tooltip = 'Click to start voice coding dictation (Ctrl+Alt+V)';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    const startCmd = vscode.commands.registerCommand('voiceCoding.startRecording', async () => {
        if (isRecording) {
            vscode.window.showInformationMessage('Dictation already in progress.');
            return;
        }
        isRecording = true;
        statusBarItem.text = '$(record) Recording...';
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
        vscode.window.showInformationMessage('🎤 Voice Coding active. Speak your instructions...');
    });

    const stopCmd = vscode.commands.registerCommand('voiceCoding.stopRecording', async () => {
        if (!isRecording) return;
        isRecording = false;
        statusBarItem.text = '$(sync~spin) Processing...';
        statusBarItem.backgroundColor = undefined;
        
        // Mock / trigger backend API call
        const config = vscode.workspace.getConfiguration('voiceCoding');
        const backendUrl = config.get<string>('backendUrl') || 'http://localhost:8000';
        
        vscode.window.showInformationMessage(`Synthesizing voice command with backend at ${backendUrl}...`);
        statusBarItem.text = '$(mic) Voice Code';
    });

    const indexCmd = vscode.commands.registerCommand('voiceCoding.indexWorkspace', async () => {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders || workspaceFolders.length === 0) {
            vscode.window.showWarningMessage('No workspace open to index.');
            return;
        }
        const rootPath = workspaceFolders[0].uri.fsPath;
        vscode.window.showInformationMessage(`Indexing workspace: ${rootPath}...`);
    });

    context.subscriptions.push(startCmd, stopCmd, indexCmd);
}

export function deactivate() {
    if (statusBarItem) {
        statusBarItem.dispose();
    }
}
