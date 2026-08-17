/**
 * The Electron main process: a thin, guarded IPC surface over the engine and the
 * vault's files. Every handler either shells out to `report-maker` or touches a
 * path that has been checked to sit inside the active vault.
 */

import { mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { BrowserWindow, app, dialog, ipcMain, shell } from 'electron'
import * as engine from './engine'
import * as tree from './tree'
import * as vaults from './vaults'

const isDev = !app.isPackaged

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    show: false,
    backgroundColor: '#0b0b0c',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      // ESM preload — Electron loads .mjs when the app itself is a module.
      preload: join(__dirname, '../preload/index.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // Chromium's built-in PDF viewer renders the built report in an iframe.
      plugins: true
    }
  })

  win.on('ready-to-show', () => win.show())

  // External links open in the real browser, never inside the shell.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })

  const devServer = process.env.ELECTRON_RENDERER_URL
  if (isDev && devServer) win.loadURL(devServer)
  else win.loadFile(join(__dirname, '../renderer/index.html'))

  return win
}

function handlers(): void {
  ipcMain.handle('vault:list', () => vaults.read())
  ipcMain.handle('vault:select', (_e, path: string) => vaults.select(path))
  ipcMain.handle('vault:forget', (_e, path: string) => vaults.forget(path))

  ipcMain.handle('vault:open', async () => {
    const picked = await dialog.showOpenDialog({
      title: 'Open a vault',
      message: 'Choose the folder that holds report-maker.toml',
      properties: ['openDirectory', 'createDirectory']
    })
    if (picked.canceled || !picked.filePaths[0]) return { ok: false as const, reason: 'cancelled' }
    const path = picked.filePaths[0]
    if (!engine.isVault(path)) {
      // Not a vault yet — offer to make it one rather than failing at it.
      const answer = await dialog.showMessageBox({
        type: 'question',
        message: 'That folder is not a vault yet.',
        detail: `${path} has no report-maker.toml. Initialise it as a vault?`,
        buttons: ['Initialise', 'Cancel'],
        defaultId: 0,
        cancelId: 1
      })
      if (answer.response !== 0) return { ok: false as const, reason: 'not-a-vault' }
      const result = await engine.run(path, ['init'])
      if (result.code !== 0) return { ok: false as const, reason: result.stderr || result.stdout }
    }
    return { ok: true as const, list: await vaults.add(path) }
  })

  // Creating a vault is picking where it should live and letting the engine
  // scaffold it — the app never writes a vault's structure itself.
  ipcMain.handle('vault:create', async () => {
    const picked = await dialog.showSaveDialog({
      title: 'Create a vault',
      message: 'Choose a name and a place for the new vault folder',
      buttonLabel: 'Create',
      nameFieldLabel: 'Vault name',
      defaultPath: join(app.getPath('documents'), 'Reports'),
      properties: ['createDirectory']
    })
    if (picked.canceled || !picked.filePath) return { ok: false as const, reason: 'cancelled' }

    await mkdir(picked.filePath, { recursive: true })
    const result = await engine.run(picked.filePath, ['init'])
    if (result.code !== 0) return { ok: false as const, reason: result.stderr || result.stdout }
    return { ok: true as const, list: await vaults.add(picked.filePath) }
  })

  ipcMain.handle('engine:where', () => engine.describe())

  ipcMain.handle('tree:read', (_e, vault: string) => tree.tree(vault))
  ipcMain.handle('file:read', (_e, vault: string, path: string) => tree.read(vault, path))
  ipcMain.handle('file:write', (_e, vault: string, path: string, text: string) =>
    tree.write(vault, path, text)
  )
  ipcMain.handle('file:bytes', (_e, vault: string, path: string) => tree.bytes(vault, path))
  ipcMain.handle('file:exists', (_e, vault: string, path: string) => tree.exists(vault, path))
  ipcMain.handle('file:reveal', (_e, vault: string, path: string) => {
    shell.showItemInFolder(tree.within(vault, path))
  })

  ipcMain.handle('engine:run', (_e, vault: string, args: string[]) => engine.run(vault, args))
  ipcMain.handle('engine:json', (_e, vault: string, args: string[]) => engine.json(vault, args))
  ipcMain.handle('engine:manifest', (_e, vault: string) => engine.manifest(vault))
}

/**
 * A vault named on the command line — `report-maker-app ~/Documents/Reports` —
 * or in RM_OPEN_VAULT. Opening a folder is the whole interaction model, so being
 * able to name one at launch is what makes the app scriptable and what the smoke
 * test uses.
 */
function requestedVault(): string | null {
  const fromEnv = process.env.RM_OPEN_VAULT
  if (fromEnv && engine.isVault(fromEnv)) return resolve(fromEnv)

  // argv holds the electron binary, possibly the app path in dev, then the rest.
  for (const arg of process.argv.slice(1)) {
    if (arg.startsWith('-')) continue
    const path = resolve(arg)
    if (engine.isVault(path)) return path
  }
  return null
}

app.whenReady().then(async () => {
  handlers()

  const requested = requestedVault()
  if (requested) await vaults.add(requested)

  const win = createWindow()

  // `npm run smoke` builds, launches, captures the window and exits — the only
  // way to check the layout of a desktop app without a human looking at it.
  const shot = process.env.RM_SCREENSHOT
  if (shot) {
    win.webContents.once('did-finish-load', () => {
      setTimeout(async () => {
        const image = await win.webContents.capturePage()
        await (await import('node:fs/promises')).writeFile(shot, image.toPNG())
        app.exit(0)
      }, Number(process.env.RM_SCREENSHOT_DELAY ?? 2500))
    })
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
