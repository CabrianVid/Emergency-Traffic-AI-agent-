import { useEffect, useMemo, useRef, useState } from 'react'
import { nearestRoad } from './model'
import type { Geometry, Point } from './model'

export interface CallDraft { x: number; y: number; priority: string; needs_als: boolean; note: string }

/** Where the dispatcher logs an emergency. The location comes from the map or a street name. */
export function NewCall({ geometry, point, onPoint, busy, onSubmit, onClose }: {
  geometry: Geometry; point: Point | null; onPoint: (point: Point, show: boolean) => void; busy: boolean
  onSubmit: (call: CallDraft) => void; onClose: () => void
}) {
  const [street, setStreet] = useState('')
  const [priority, setPriority] = useState('critical')
  const [als, setAls] = useState(false)
  const [note, setNote] = useState('')
  // The middle of each street's longest piece.
  const streets = useMemo(() => {
    const best = new Map<string, { length: number; at: Point }>()
    for (const road of Object.values(geometry.edges)) {
      if (!road.name) continue
      for (let i = 1; i < road.shape.length; i++) {
        const [a, b] = [road.shape[i - 1], road.shape[i]]
        const length = Math.hypot(b[0] - a[0], b[1] - a[1])
        if (length > (best.get(road.name)?.length ?? 0)) best.set(road.name, { length, at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] })
      }
    }
    return new Map([...best].map(([name, { at }]) => [name.toLocaleLowerCase('sl'), { name, at }]))
  }, [geometry])
  const near = useMemo(() => point ? nearestRoad(geometry, point) : null, [geometry, point])
  // A click on the map fills in the street; typing a street does not overwrite itself.
  const typed = useRef(false)
  useEffect(() => {
    if (near && !typed.current) setStreet(near.name)
    typed.current = false
  }, [near])

  function find(value: string) {
    setStreet(value)
    const found = streets.get(value.trim().toLocaleLowerCase('sl'))
    if (found) { typed.current = true; onPoint(found.at, true) }
  }
  return <form className="inspector new-call" aria-label="New emergency" onSubmit={(event) => {
    event.preventDefault()
    if (point && !busy) onSubmit({ x: point[0], y: point[1], priority, needs_als: als, note: note.trim() })
  }}>
    <header><h2>New emergency</h2><button type="button" aria-label="Close form" onClick={onClose}>×</button></header>
    <div className="fields">
      <label>Street<input autoFocus list="streets" value={street} placeholder="Type a street, or click the map" onChange={(event) => find(event.target.value)}/></label>
      <datalist id="streets">{[...streets.values()].map(({ name }) => <option key={name} value={name}/>)}</datalist>
      <p className={`where ${point ? '' : 'missing'}`}>{point ? `Location set: ${near?.name || 'unnamed road'}` : 'Click the map where the emergency is'}</p>
      <fieldset><legend>Priority</legend>
        {[['critical', '1', 'Life at risk'], ['high', '2', 'Urgent'], ['routine', '3', 'Can wait']].map(([value, number, label]) =>
          <label key={value} className={priority === value ? 'on' : ''}><input type="radio" name="priority" checked={priority === value} onChange={() => setPriority(value)}/>
            <b className={`prio p${number}`}>{number}</b>{label}</label>)}
      </fieldset>
      <label>What happened<input value={note} maxLength={160} placeholder="For example: chest pain, 60 years" onChange={(event) => setNote(event.target.value)}/></label>
      <label className="check"><input type="checkbox" checked={als} onChange={(event) => setAls(event.target.checked)}/>Needs an advanced life support crew (ALS)</label>
    </div>
    <button className="submit" disabled={!point || busy}>{busy ? 'Comparing plans…' : 'Log call and get a suggestion'}</button>
  </form>
}
