"use client"

import * as React from "react"
import { RotateCcwIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const HEX = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i

/** `#ABC` and `abc` and `#aabbcc` all mean the same colour; the pack stores one
 *  spelling of it. Returns null for anything that is not a hex colour. */
function normalise(text: string): string | null {
  const match = HEX.exec(text.trim())
  if (!match) return null
  const digits = match[1].toLowerCase()
  return digits.length === 3
    ? `#${digits[0]}${digits[0]}${digits[1]}${digits[1]}${digits[2]}${digits[2]}`
    : `#${digits}`
}

function same(a: string, b: string): boolean {
  if (a.trim() === b.trim()) return true
  const left = normalise(a)
  return left !== null && left === normalise(b)
}

type Props = {
  /** Human name of the token, e.g. "Accent". */
  label: string
  /** The current value, as stored in the pack. */
  value: string
  /** Called with a normalised `#rrggbb` — never with a half-typed value. */
  onChange: (hex: string) => void
  /** The dotted key this edits, e.g. `colors.accent`. Shown under the label. */
  hint?: string
  /** True when the value comes from the default pack rather than this one. */
  inherited?: boolean
  /** Offered as a reset control when the field carries an override. */
  onReset?: () => void
  disabled?: boolean
  id?: string
  className?: string
}

/**
 * One brand colour. The brand studio stacks about twenty of these, so it stays
 * a single row: swatch, name, hex.
 *
 * The two inputs disagree about when a value exists — the OS picker emits a
 * colour on every drag, while a hex typed by hand is nonsense until the last
 * character lands. So the text stays local until it parses, and `onChange`
 * fires only with a value the pack can actually hold.
 */
function ColorField({
  label,
  value,
  onChange,
  hint,
  inherited,
  onReset,
  disabled,
  id,
  className,
}: Props) {
  const generated = React.useId()
  const fieldId = id ?? generated
  const [draft, setDraft] = React.useState(value)
  const hex = normalise(draft)

  // Follow the pack when it changes underneath us — a pack switch, an import, a
  // reset — but never rewrite what is being typed into the same colour.
  React.useEffect(() => {
    setDraft((current) => (same(current, value) ? current : value))
  }, [value])

  const commit = (next: string): void => {
    const parsed = normalise(next)
    if (parsed && !same(parsed, value)) onChange(parsed)
  }

  return (
    <div
      className={cn("flex items-center gap-2", className)}
      data-slot="color-field"
    >
      <input
        type="color"
        id={fieldId}
        disabled={disabled}
        // The picker only speaks #rrggbb; an unparseable value shows as black,
        // and the hex field next to it says why.
        value={hex ?? "#000000"}
        onChange={(event) => {
          setDraft(event.target.value)
          commit(event.target.value)
        }}
        className="size-7 shrink-0 cursor-pointer rounded-md border border-input bg-transparent p-0.5 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 [&::-webkit-color-swatch]:rounded-[3px] [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch-wrapper]:p-0"
      />

      <div className="min-w-0 flex-1">
        <Label
          htmlFor={fieldId}
          className={cn("text-xs", inherited && "text-muted-foreground")}
        >
          <span className="truncate">{label}</span>
          {inherited && (
            <Badge variant="outline" className="px-1 py-0 text-[10px]">
              default
            </Badge>
          )}
        </Label>
        {hint && (
          <p className="truncate font-mono text-[10px] text-muted-foreground">
            {hint}
          </p>
        )}
      </div>

      <Input
        value={draft}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        aria-label={`${label} hex value`}
        aria-invalid={hex === null}
        onChange={(event) => {
          setDraft(event.target.value)
          commit(event.target.value)
        }}
        onBlur={() => setDraft(hex ?? value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault()
            commit(draft)
            event.currentTarget.blur()
          }
          if (event.key === "Escape") {
            event.preventDefault()
            setDraft(value)
            event.currentTarget.blur()
          }
        }}
        className="h-7 w-[5.5rem] shrink-0 px-2 font-mono text-xs"
      />

      {onReset && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          disabled={disabled || inherited}
          onClick={onReset}
          title="Reset to the default pack's value"
          className="shrink-0"
        >
          <RotateCcwIcon />
          <span className="sr-only">Reset {label}</span>
        </Button>
      )}
    </div>
  )
}

export { ColorField, normalise as normaliseHex }
