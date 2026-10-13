export interface PublicEvent {
  id: number; title: string; kind: 'event' | 'concert' | 'webinar'; description: string;
  location: string; link: string; starts_at: string; ends_at: string; timezone: string;
  all_day: boolean; resources: { id: number; name: string }[];
}

export function validEvent(event: PublicEvent): boolean {
  if (!event || !Number.isSafeInteger(event.id) || event.id < 1 || typeof event.title !== 'string'
    || !['event', 'concert', 'webinar'].includes(event.kind) || typeof event.description !== 'string'
    || typeof event.location !== 'string' || typeof event.link !== 'string'
    || (event.link !== '' && !/^https?:\/\//i.test(event.link))
    || typeof event.starts_at !== 'string' || typeof event.ends_at !== 'string'
    || !/(Z|[+-]\d{2}:\d{2})$/.test(event.starts_at) || !/(Z|[+-]\d{2}:\d{2})$/.test(event.ends_at)
    || !Number.isFinite(Date.parse(event.starts_at)) || Date.parse(event.ends_at) <= Date.parse(event.starts_at)
    || !Number.isFinite(Date.parse(event.ends_at)) || typeof event.all_day !== 'boolean'
    || !Array.isArray(event.resources) || !event.resources.every(r => Number.isSafeInteger(r.id) && r.id > 0 && typeof r.name === 'string')) return false;
  try { new Intl.DateTimeFormat('fr-FR', { timeZone: event.timezone }).format(); return typeof event.timezone === 'string'; }
  catch { return false; }
}

