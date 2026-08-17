import { useEffect, useRef } from 'react'
import { EditorState, type Extension } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { basicSetup } from 'codemirror'
import { languageFor } from '@/lib/typst'

type Props = {
  path: string | null
  text: string
  onChange: (text: string) => void
  onSave: () => void
  onBuild: () => void
}

/**
 * CodeMirror, recreated whenever the open file changes and left alone otherwise.
 * The parent owns the text; this view reports edits upward and never re-seeds
 * itself from props, which is what keeps the cursor where the writer put it.
 */
export function Editor({ path, text, onChange, onSave, onBuild }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const handlers = useRef({ onChange, onSave, onBuild })
  handlers.current = { onChange, onSave, onBuild }

  useEffect(() => {
    if (!host.current || path === null) return

    const extensions: Extension[] = [
      basicSetup,
      ...languageFor(path),
      EditorView.lineWrapping,
      keymap.of([
        indentWithTab,
        { key: 'Mod-s', preventDefault: true, run: () => (handlers.current.onSave(), true) },
        { key: 'Mod-b', preventDefault: true, run: () => (handlers.current.onBuild(), true) }
      ]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) handlers.current.onChange(update.state.doc.toString())
      })
    ]

    const instance = new EditorView({
      state: EditorState.create({ doc: text, extensions }),
      parent: host.current
    })
    view.current = instance
    instance.focus()
    return () => {
      instance.destroy()
      view.current = null
    }
    // Deliberately keyed on the path alone: a new document means a new view, an
    // edit does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  if (path === null) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-muted-foreground">
        <div>
          <p className="text-sm">Nothing open.</p>
          <p className="mt-1 text-xs">
            Pick a <span className="font-mono">main.typ</span> or{' '}
            <span className="font-mono">sources.yml</span> from the tree.
          </p>
        </div>
      </div>
    )
  }

  return <div ref={host} className="h-full overflow-hidden" />
}
