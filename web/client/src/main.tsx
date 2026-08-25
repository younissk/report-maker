import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/App'
import { Reports } from '@/screens/Reports'
import { Write } from '@/screens/Write'
import { Read } from '@/screens/Read'
import { Evidence } from '@/screens/Evidence'
import '@/styles.css'

/**
 * The mount, and nothing else.
 *
 * The four panes are handed to `<App>` here and take no props of their own:
 * every pane reads the session, the selected report, the check result and the
 * build action from `useApp()`. Adding a pane is one line in this file.
 */
const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    <App
      panes={{
        reports: <Reports />,
        write: <Write />,
        read: <Read />,
        evidence: <Evidence />,
      }}
    />
  </StrictMode>
)
