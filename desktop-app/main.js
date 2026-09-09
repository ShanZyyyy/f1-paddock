const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 480,
    minHeight: 640,
    backgroundColor: '#0a0e14',
    title: 'Paddock GP',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
      // race3d-demo.html loads Three.js as ES modules (import statements),
      // which Chromium treats as cross-origin fetches under file:// and
      // blocks by default. This is a fully offline, self-authored app with
      // no remote/untrusted content, so relaxing this is low-risk here.
      webSecurity: false
    }
  });

  Menu.setApplicationMenu(null);

  win.loadFile(path.join(__dirname, 'app', 'game.html'));

  // Open any target="_blank" links (there are none currently, but stay safe) in the OS browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
