/**
 * The Electron main process: a thin, guarded IPC surface over the engine and the
 * vault's files. Every handler either shells out to `report-maker` or touches a
 * path that has been checked to sit inside the active vault.
 */

import { mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { BrowserWindow, Menu, app, dialog, ipcMain, shell } from 'electron'
import type {
  DeepPartial,
  GitState,
  MenuCommand,
  Settings,
  VaultList
} from '../shared/types'
import * as engine from './engine'
import * as env from './env'
import * as fonts from './fonts'
import * as settings from './settings'
import * as tree from './tree'
import * as vaults from './vaults'
import * as watch from './watch'

const isDev = !app.isPackaged

/**
 * A vault named on the command line, resolved once at launch. Held here because
 * the menu and the first `vault:list` both have to know it won over the startup
 * preference — see `requestedVault()` below.
 */
let launchVault: string | null = null

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

  // A watcher outlives nothing. Its output has nowhere to go once the window is
  // closed, and a rebuild loop nobody can see is a rebuild loop nobody can stop.
  win.on('closed', () => watch.stop())

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

// ── the application menu ─────────────────────────────────────────────────────

/**
 * Send a menu item to the window that has to act on it.
 *
 * The menu changes state the renderer owns — which file is open, which vault is
 * current, whether a build is running — so it dispatches rather than acts. Main
 * keeps only what it can finish alone: the diagnostics box below, and the
 * folder dialogs, which the renderer reaches through the same `vaults.open()`
 * it already calls from the Welcome screen. One code path per action, whichever
 * end it was triggered from.
 */
function dispatch(command: MenuCommand): void {
  const win = BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0]
  win?.webContents.send('menu:command', command)
}

/** `report-maker doctor`, in a box. The one thing the app can say about its own
 *  installation, and the answer to "it works in my terminal". */
async function showDiagnostics(): Promise<void> {
  const list = await vaults.read()
  const found = await engine.doctor(list.current)
  const probe = found.path
  await dialog.showMessageBox({
    type: found.code === 0 ? 'info' : 'warning',
    message: 'report-maker doctor',
    detail: [
      `engine    ${found.engine}`,
      `version   ${found.version ?? 'unavailable (this engine predates --version)'}`,
      '',
      found.doctor || '(no output)',
      '',
      probe
        ? `PATH      ${probe.ok ? `recovered from ${probe.shell}` : probe.detail}` +
          `\n          +${probe.fromShell.length} from the shell, ` +
          `+${probe.fromFallback.length} from the fallback list` +
          `\n\n${probe.path.split(':').join('\n')}`
        : 'PATH      not yet recovered'
    ].join('\n'),
    buttons: ['Done']
  })
}

/**
 * The application menu.
 *
 * Deliberately minimal: five menus, and the only items that are not a standard
 * role are the ones this app actually has. Open Recent is the reason it exists
 * at all — a list of vaults that is only reachable from a dropdown inside the
 * window is not reachable at all before a window has a vault in it.
 *
 * The three renderer-owned items carry **no accelerator**. `⌘N`, `⌘S` and `⌘B`
 * are already bound in `commands.ts`, and a menu accelerator swallows the
 * keystroke before the page sees it — registering them here would silently
 * break the shortcuts it is meant to advertise. They move onto the menu once
 * the renderer takes its commands from `menu.on` instead of from keydown.
 */
function buildMenu(list: VaultList): void {
  const recent = list.entries.slice(0, 10).map((entry) => ({
    label: entry.missing ? `${entry.name} (missing)` : entry.name,
    sublabel: entry.path,
    toolTip: entry.path,
    // A missing vault stays visible and stays disabled: seeing that the app
    // still knows about the folder on the drive you unplugged is the point.
    enabled: !entry.missing,
    click: () => dispatch({ kind: 'select-vault', path: entry.path })
  }))

  const template: Electron.MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin'
      ? ([{ role: 'appMenu' }] as Electron.MenuItemConstructorOptions[])
      : []),
    {
      label: 'File',
      submenu: [
        { label: 'New Report…', click: () => dispatch({ kind: 'new-report' }) },
        { type: 'separator' },
        {
          label: 'Open Vault…',
          accelerator: 'CmdOrCtrl+Shift+O',
          click: () => dispatch({ kind: 'open-vault' })
        },
        {
          label: 'Open Recent',
          submenu: recent.length
            ? [
                ...recent,
                { type: 'separator' as const },
                { label: 'Create a Vault…', click: () => dispatch({ kind: 'create-vault' }) }
              ]
            : [{ label: 'No vaults yet', enabled: false }]
        },
        { type: 'separator' },
        { label: 'Save', click: () => dispatch({ kind: 'save' }) },
        { label: 'Build', click: () => dispatch({ kind: 'build' }) },
        { type: 'separator' },
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' }
      ]
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [{ label: 'Engine Diagnostics…', click: () => void showDiagnostics() }]
    }
  ]

  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

/** Rebuild the menu around a list that has just changed, and hand the list back
 *  so every mutating handler stays a one-liner. */
async function withMenu(list: VaultList): Promise<VaultList> {
  buildMenu(list)
  return list
}

function handlers(): void {
  ipcMain.handle('vault:list', () => vaults.opening(launchVault))
  ipcMain.handle('vault:select', async (_e, path: string) => withMenu(await vaults.select(path)))
  ipcMain.handle('vault:forget', async (_e, path: string) => withMenu(await vaults.forget(path)))
  ipcMain.handle('vault:pin', async (_e, path: string, pinned: boolean) =>
    withMenu(await vaults.pin(path, pinned))
  )

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
    return { ok: true as const, list: await withMenu(await vaults.add(path)) }
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
    return { ok: true as const, list: await withMenu(await vaults.add(picked.filePath)) }
  })

  ipcMain.handle('engine:where', () => engine.describe())
  ipcMain.handle('engine:version', () => engine.version())
  ipcMain.handle('engine:doctor', (_e, vault: string | null) => engine.doctor(vault))

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
  // Hand the file to whatever the system opens it with — an archived snapshot
  // belongs in a browser, not selected in Finder. Same path guard as every other
  // file channel: the renderer sends paths back, so none of them are trusted.
  ipcMain.handle('file:open', (_e, vault: string, path: string) =>
    shell.openPath(tree.within(vault, path))
  )

  ipcMain.handle('engine:run', (_e, vault: string, args: string[]) => engine.run(vault, args))
  ipcMain.handle('engine:json', (_e, vault: string, args: string[]) => engine.json(vault, args))
  ipcMain.handle('engine:manifest', (_e, vault: string) => engine.manifest(vault))

  ipcMain.handle('settings:get', () => settings.read())
  ipcMain.handle('settings:set', (_e, patch: DeepPartial<Settings>) => settings.update(patch))
  ipcMain.handle('settings:reset', () => settings.reset())

  // The watcher belongs to the window that asked for it, so its output goes back
  // to that window and closing it takes the child process with it.
  ipcMain.handle('watch:start', (event, vault: string, target: string) => {
    const win = BrowserWindow.fromWebContents(event.sender)
    if (!win) throw new Error('no window to stream a watch run to')
    watch.start(win, vault, target)
  })
  ipcMain.handle('watch:stop', () => watch.stop())

  // Git is the engine's business, not the app's: `sync` is where the rules about
  // upstreams, detached heads and being behind the remote are written down once.
  ipcMain.handle('git:state', (_e, vault: string) =>
    engine.json<GitState>(vault, ['sync', '--status', '--json'])
  )
  ipcMain.handle('git:sync', (_e, vault: string, push: boolean) =>
    engine.run(vault, ['sync', ...(push ? ['--push'] : [])])
  )

  ipcMain.handle('fonts:list', () => fonts.list())
}

/**
 * A vault named on the command line — `report-maker-app ~/Documents/Reports` —
 * or in RM_OPEN_VAULT. Opening a folder is the whole interaction model, so being
 * able to name one at launch is what makes the app scriptable and what the smoke
 * test uses.
 *
 * It beats the `startup.reopenLast` preference, and has to: a caller that named
 * a vault has said which one it wants, and a smoke run that silently opened
 * yesterday's vault instead would still pass while testing the wrong thing.
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
  // Kick the PATH recovery off before anything can spawn, and do not wait for
  // it: a login shell that hangs must cost a slow first command, never a blank
  // window. Everything that spawns awaits the same cached promise.
  void env.hydrate()

  handlers()

  launchVault = requestedVault()
  if (launchVault) await vaults.add(launchVault)

  buildMenu(await vaults.read())

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

// The last line of defence against a stray python3/typst outliving the app.
app.on('will-quit', () => watch.stop())
