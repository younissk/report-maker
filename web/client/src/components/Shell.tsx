import { useEffect, useState, type ReactNode } from 'react'
import {
  BookOpen,
  Files,
  Hammer,
  Loader2,
  PanelLeft,
  PanelRight,
  PenLine,
  Quote,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn, useIsDesktop, useKeyboardOpen } from '@/lib/utils'

/**
 * The frame everything else is dropped into.
 *
 * One component tree serves both layouts. Below 1024px it shows one pane at a
 * time under a bottom tab bar; at 1024px and above the same four panes sit side
 * by side. The panes are always mounted either way — an editor that loses its
 * undo history because somebody looked at the PDF is an editor people stop
 * trusting — so switching tabs on a phone toggles `hidden`, nothing more.
 *
 * One consequence worth knowing: a pane that was `display: none` has no
 * measurable geometry. CodeMirror in particular has to `requestMeasure()` when
 * its pane comes back. That is the editor's job, not the shell's, but it is the
 * shell that creates the condition.
 *
 * The shell holds no state about a vault and asks no questions of its own. It
 * is told what to render and which tab is current.
 */

export type ShellTab = 'reports' | 'write' | 'read' | 'evidence'

export const SHELL_TABS: { id: ShellTab; label: string; icon: typeof Files }[] = [
  { id: 'reports', label: 'Reports', icon: Files },
  { id: 'write', label: 'Write', icon: PenLine },
  { id: 'read', label: 'Read', icon: BookOpen },
  { id: 'evidence', label: 'Evidence', icon: Quote },
]

export type ShellProps = {
  tab: ShellTab
  onTabChange: (tab: ShellTab) => void

  /** The report's own title. User text — React escapes it, and so must you. */
  title?: string
  /** Its group, its status, whatever the second line should say. */
  subtitle?: string

  /** The build action. Absent means there is nothing to build yet. */
  onBuild?: () => void
  building?: boolean
  buildLabel?: string

  /** Slots in the top bar, either side of the title. */
  leading?: ReactNode
  actions?: ReactNode

  /**
   * A full-width strip under the top bar. This is where the first-run note goes
   * — the one that says the report you were handed is unedited scaffolding.
   */
  banner?: ReactNode

  reports: ReactNode
  write: ReactNode
  read: ReactNode
  evidence: ReactNode

  /** A desktop status bar. Hidden on a phone, where the tab bar owns that edge. */
  footer?: ReactNode

  /**
   * A count on a tab — findings on Evidence, say. Rendered as a number up to 99
   * and as a dot above that, because the badge is a signal, not a readout.
   */
  counts?: Partial<Record<ShellTab, number>>
  /** Tabs whose count should read as a problem rather than an inventory. */
  alerts?: Partial<Record<ShellTab, boolean>>
}

export function Shell({
  tab,
  onTabChange,
  title,
  subtitle,
  onBuild,
  building = false,
  buildLabel = 'Build',
  leading,
  actions,
  banner,
  reports,
  write,
  read,
  evidence,
  footer,
  counts,
  alerts,
}: ShellProps) {
  const desktop = useIsDesktop()
  const keyboard = useKeyboardOpen()

  // Desktop panel state. The evidence panel starts closed on a laptop and open
  // on a wide screen: four columns at 1024px leaves the editor too narrow to
  // write in, which is the pane the product is actually for.
  const [sidebar, setSidebar] = useState(true)
  const [evidenceOpen, setEvidenceOpen] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 1536px)').matches
  )

  // `tab` stays the single "what do you want to look at" signal in both layouts;
  // on a desktop the shell translates it into opening the matching panel rather
  // than swapping the whole screen.
  useEffect(() => {
    if (!desktop) return
    if (tab === 'evidence') setEvidenceOpen(true)
    if (tab === 'reports') setSidebar(true)
  }, [desktop, tab])

  const buildButton = onBuild ? (
    <Button
      size={desktop ? 'sm' : 'default'}
      onClick={onBuild}
      disabled={building}
      aria-label={building ? 'Building' : buildLabel}
      className="shrink-0"
    >
      {building ? (
        <Loader2 className="animate-spin" aria-hidden />
      ) : (
        <Hammer aria-hidden />
      )}
      <span className={cn(building && 'sr-only lg:not-sr-only')}>
        {building ? 'Building…' : buildLabel}
      </span>
    </Button>
  ) : null

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      {/* ── top bar ───────────────────────────────────────────────────────── */}
      <header className="safe-t safe-x shrink-0 border-b bg-background">
        <div className="flex min-h-[var(--topbar-h)] items-center gap-2 px-3 py-2 lg:px-4">
          {desktop && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setSidebar((open) => !open)}
              aria-pressed={sidebar}
              aria-label={sidebar ? 'Hide the report list' : 'Show the report list'}
            >
              <PanelLeft aria-hidden />
            </Button>
          )}
          {leading}

          <div className="min-w-0 flex-1">
            <div className="truncate text-sm leading-tight font-semibold lg:text-[13px]">
              {title ?? 'report-maker'}
            </div>
            {subtitle ? (
              <div className="truncate text-[11px] leading-tight text-muted-foreground">
                {subtitle}
              </div>
            ) : (
              !title && (
                <div className="truncate text-[11px] leading-tight text-muted-foreground">
                  Something is either cited, or it is an opinion.
                </div>
              )
            )}
          </div>

          {actions}
          {buildButton}

          {desktop && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setEvidenceOpen((open) => !open)}
              aria-pressed={evidenceOpen}
              aria-label={evidenceOpen ? 'Hide the evidence panel' : 'Show the evidence panel'}
            >
              <PanelRight aria-hidden />
            </Button>
          )}
        </div>
        {banner}
      </header>

      {/* ── panes ─────────────────────────────────────────────────────────── */}
      {desktop ? (
        <div className="safe-x flex min-h-0 flex-1">
          <Pane
            className={cn('w-64 shrink-0 border-r', !sidebar && 'hidden')}
            label="Reports"
          >
            {reports}
          </Pane>
          <Pane className="min-w-0 flex-1" label="Write">
            {write}
          </Pane>
          <Pane className="min-w-0 flex-1 border-l" label="Read">
            {read}
          </Pane>
          <Pane
            className={cn('w-80 shrink-0 border-l', !evidenceOpen && 'hidden')}
            label="Evidence"
          >
            {evidence}
          </Pane>
        </div>
      ) : (
        <div className="safe-x relative min-h-0 flex-1">
          <MobilePane active={tab === 'reports'} label="Reports">
            {reports}
          </MobilePane>
          <MobilePane active={tab === 'write'} label="Write">
            {write}
          </MobilePane>
          <MobilePane active={tab === 'read'} label="Read">
            {read}
          </MobilePane>
          <MobilePane active={tab === 'evidence'} label="Evidence">
            {evidence}
          </MobilePane>
        </div>
      )}

      {/* ── the bottom edge ───────────────────────────────────────────────── */}
      {desktop
        ? footer && (
            <footer className="safe-x shrink-0 border-t bg-background">{footer}</footer>
          )
        : /* The bar is in the flow, not fixed, and it goes away entirely while
             the soft keyboard is up. A fixed bar over a keyboard covers the word
             being typed on Android and lands in the wrong place on iOS; hiding
             it also hands the editor back 56 valuable pixels. */
          !keyboard && (
            <TabBar tab={tab} onTabChange={onTabChange} counts={counts} alerts={alerts} />
          )}
    </div>
  )
}

function Pane({
  className,
  label,
  children,
}: {
  className?: string
  label: string
  children: ReactNode
}) {
  return (
    <section aria-label={label} className={cn('flex min-h-0 flex-col', className)}>
      {children}
    </section>
  )
}

/**
 * All four are mounted; three are `hidden`. `inert` keeps the hidden ones out of
 * the tab order and off a screen reader, which `display: none` already does —
 * belt and braces, because a pane that is only visually hidden and still
 * focusable is how a keyboard user ends up typing into a screen nobody can see.
 */
function MobilePane({
  active,
  label,
  children,
}: {
  active: boolean
  label: string
  children: ReactNode
}) {
  return (
    <section
      aria-label={label}
      hidden={!active}
      inert={!active}
      className={cn('absolute inset-0 flex min-h-0 flex-col', !active && 'hidden')}
    >
      {children}
    </section>
  )
}

function TabBar({
  tab,
  onTabChange,
  counts,
  alerts,
}: {
  tab: ShellTab
  onTabChange: (tab: ShellTab) => void
  counts?: Partial<Record<ShellTab, number>>
  alerts?: Partial<Record<ShellTab, boolean>>
}) {
  return (
    <nav
      aria-label="Sections"
      className="safe-b safe-x shrink-0 border-t bg-background"
    >
      <div className="flex items-stretch">
        {SHELL_TABS.map(({ id, label, icon: Icon }) => {
          const current = id === tab
          const count = counts?.[id] ?? 0
          const alert = alerts?.[id] ?? false
          return (
            <button
              key={id}
              type="button"
              onClick={() => onTabChange(id)}
              aria-current={current ? 'page' : undefined}
              className={cn(
                // 56px tall, a quarter of 375px wide: comfortably past the 44px
                // floor in both directions.
                'relative flex min-h-[var(--tabbar-h)] flex-1 flex-col items-center justify-center gap-0.5 px-1 py-1.5 text-[11px] font-medium transition-colors outline-none select-none',
                'focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset',
                current ? 'text-foreground' : 'text-muted-foreground active:bg-accent'
              )}
            >
              <span className="relative">
                <Icon className="size-5" aria-hidden />
                {count > 0 && (
                  <span
                    className={cn(
                      'absolute -top-1.5 -right-2 min-w-4 rounded-full px-1 text-[10px] leading-4 font-semibold tabular-nums',
                      alert
                        ? 'bg-destructive text-white'
                        : 'bg-secondary text-secondary-foreground'
                    )}
                  >
                    {count > 99 ? '99+' : count}
                  </span>
                )}
              </span>
              <span>{label}</span>
              {/* The active tab is named by a line as well as by colour, so it
                  survives a colour-blind reader and a bright day outdoors. */}
              <span
                aria-hidden
                className={cn(
                  'absolute inset-x-3 top-0 h-0.5 rounded-full',
                  current ? 'bg-foreground' : 'bg-transparent'
                )}
              />
            </button>
          )
        })}
      </div>
    </nav>
  )
}

export { useIsDesktop, useKeyboardOpen }
