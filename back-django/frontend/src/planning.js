// Dragging proposes a move. Only the authenticated, CSRF-protected confirmation saves it.
export function dropUrl(handle, target, origin) {
  const url = new URL(handle.href, origin);
  if (url.origin !== origin) return null;
  const allDay = handle.allDay === 'true';
  if ((target.kind === 'timed' && allDay) || (target.kind === 'all-day' && !allDay)) return null;
  url.searchParams.set('date', target.date);
  url.searchParams.set('timezone', target.timezone);
  url.searchParams.set('version', handle.version);
  if (target.hour !== undefined && !allDay) url.searchParams.set('hour', target.hour);
  return url.href;
}

export function blockPosition({offset, duration, lane, lanes}, dayMinutes) {
  const values = [offset, duration, lane, lanes, dayMinutes];
  if (!values.every(Number.isFinite) || offset < 0 || duration <= 0 || offset >= dayMinutes || lane < 0 || lanes < 1 || lane >= lanes) return null;
  const ratio = lane / lanes;
  return {
    top: `${offset}px`,
    height: `${Math.min(dayMinutes - offset, Math.max(6, duration))}px`,
    left: `calc(${ratio * 100}% + ${44 * (1 - ratio) + 2}px)`,
    width: `calc(${100 / lanes}% - ${44 / lanes + 4}px)`,
  };
}

function positionClocks() {
  document.querySelectorAll('[data-planning-clock]').forEach(clock => {
    const days = [...clock.querySelectorAll('[data-clock-day]')];
    const columns = days.map(day => `minmax(${Math.max(210, Number(day.dataset.lanes) * 110 + 44)}px, 1fr)`).join(' ');
    clock.querySelectorAll('.scheduler-clock-headings, .scheduler-clock-alldays, .scheduler-clock-days').forEach(grid => { grid.style.gridTemplateColumns = columns; });
    clock.style.minWidth = `${days.reduce((sum, day) => sum + Math.max(210, Number(day.dataset.lanes) * 110 + 44), 0)}px`;
    days.forEach(day => {
      const minutes = Number(day.dataset.minutes);
      day.style.height = `${minutes}px`;
      day.querySelectorAll('[data-clock-slot]').forEach(slot => {
        slot.style.top = `${Number(slot.dataset.offset)}px`;
        slot.style.height = `${Number(slot.dataset.duration)}px`;
      });
      day.querySelectorAll('[data-clock-event]').forEach(block => {
        const position = blockPosition(Object.fromEntries(['offset', 'duration', 'lane', 'lanes'].map(key => [key, Number(block.dataset[key])])), minutes);
        if (!position) return;
        Object.assign(block.style, position);
        block.classList.toggle('is-tiny', Number(block.dataset.duration) < 34);
      });
    });
    clock.classList.add('has-clock-layout');
    const firstOffset = Math.min(8 * 60, ...[...clock.querySelectorAll('[data-clock-event]')].map(block => Number(block.dataset.offset)));
    clock.querySelector('.scheduler-clock-scroll').scrollTop = Math.max(0, firstOffset - 60);
  });
}

if (typeof document !== 'undefined') {
  positionClocks();
  const handles = document.querySelectorAll('[data-planning-move]');
  const targets = document.querySelectorAll('[data-planning-drop]');
  const status = document.querySelector('[data-planning-status]');
  let source = null;
  let activeTarget = null;
  const clear = () => {
    source?.classList.remove('is-dragging');
    source?.closest('[data-clock-event]')?.classList.remove('is-moving');
    targets.forEach(target => target.classList.remove('is-drop-target'));
    source = null;
    activeTarget?.classList.remove('is-drop-target');
    activeTarget = null;
    if (status) status.textContent = '';
  };
  const dropTarget = (target, event) => {
    if (!target.hasAttribute('data-clock-day')) return target;
    const offset = event.clientY - target.getBoundingClientRect().top;
    return [...target.querySelectorAll('[data-clock-slot]')].reverse().find(slot => Number(slot.dataset.offset) <= offset) || target;
  };
  const proposed = (target, event) => {
    const cell = dropTarget(target, event);
    return source && dropUrl({href: source.href, allDay: source.dataset.allDay, version: source.dataset.version}, {date: cell.dataset.date, timezone: cell.dataset.timezone, hour: cell.dataset.hour, kind: target.dataset.dropKind}, location.origin);
  };
  handles.forEach(handle => {
    handle.draggable = true;
    handle.addEventListener('dragstart', event => {
      source = handle;
      handle.classList.add('is-dragging');
      handle.closest('[data-clock-event]')?.classList.add('is-moving');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', handle.dataset.eventId);
      if (status) status.textContent = 'Déposez sur un jour ou une heure. Échap annule le déplacement.';
    });
    handle.addEventListener('dragend', clear);
  });
  targets.forEach(target => {
    target.addEventListener('dragover', event => {
      if (!proposed(target, event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      activeTarget?.classList.remove('is-drop-target');
      activeTarget = dropTarget(target, event);
      activeTarget.classList.add('is-drop-target');
    });
    target.addEventListener('dragleave', () => activeTarget?.classList.remove('is-drop-target'));
    target.addEventListener('drop', event => {
      const url = proposed(target, event);
      if (!url) return;
      event.preventDefault();
      clear();
      location.assign(url);
    });
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') clear(); });
}
