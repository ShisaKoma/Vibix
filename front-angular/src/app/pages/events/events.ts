import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SiteStore } from '../../site-store';

import { PublicEvent, validEvent } from './event-contract';

@Component({ selector: 'app-events', imports: [FormsModule], templateUrl: './events.html' })
export class EventsPage {
  readonly store = inject(SiteStore);
  readonly events = signal<PublicEvent[]>([]);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly page = signal(1);
  readonly pages = signal(1);
  readonly count = signal(0);
  start = new Date().toISOString().slice(0, 10);
  end = new Date(Date.now() + 31 * 86400000).toISOString().slice(0, 10);
  kind = '';
  private requestSequence = 0;
  constructor() { void this.load(); }

  async load(page = 1) {
    const sequence = ++this.requestSequence;
    this.error.set(''); this.events.set([]); this.pages.set(1); this.count.set(0);
    const start = Date.parse(`${this.start}T00:00:00Z`), end = Date.parse(`${this.end}T00:00:00Z`);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end - start > 93 * 86400000) {
      this.error.set('Choisissez une période positive de 93 jours maximum.'); this.loading.set(false); return;
    }
    if (this.store.config.dataMode !== 'api') {
      this.error.set('Les événements sont disponibles en mode API.'); return;
    }
    if (!this.store.data()?.planningEnabled) {
      this.error.set('La section Événements est désactivée sur ce site.'); return;
    }
    this.loading.set(true);
    try {
      const params = new URLSearchParams({ start: new Date(start).toISOString(), end: new Date(end).toISOString(), page: String(page) });
      if (this.kind) params.set('kind', this.kind);
      const response = await fetch(`${this.store.config.apiBase}/sites/${this.store.config.siteId}/events/?${params}`, {
        credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(15000), headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw new Error('Les événements sont indisponibles. Réessayez.');
      const data = await response.json();
      if (data.page !== page || !Number.isSafeInteger(data.pages) || data.pages < 1
        || !Number.isSafeInteger(data.count) || data.count < 0 || !Array.isArray(data.results)
        || data.results.length > 20 || !data.results.every(validEvent)) throw new Error('Le calendrier reçu est invalide.');
      if (sequence !== this.requestSequence) return;
      this.events.set(data.results); this.page.set(page); this.pages.set(data.pages); this.count.set(data.count);
    } catch (error) {
      if (sequence === this.requestSequence) this.error.set(error instanceof Error ? error.message : 'Les événements sont indisponibles.');
    } finally { if (sequence === this.requestSequence) this.loading.set(false); }
  }

  label(kind: string) { return ({event: 'Événement', concert: 'Concert', webinar: 'Webinaire'} as Record<string, string>)[kind]; }
  date(event: PublicEvent, end = false) {
    return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'full', ...(event.all_day ? {} : {timeStyle: 'short' as const}), timeZone: event.timezone }).format(new Date(end ? event.ends_at : event.starts_at));
  }
}
