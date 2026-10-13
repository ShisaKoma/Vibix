import { Injectable, inject, signal } from '@angular/core';
import { Title, Meta } from '@angular/platform-browser';
import { CONTENT_SOURCE, RUNTIME_CONFIG, SiteContent, SiteSettings, ContentModule } from './content';

@Injectable({ providedIn: 'root' })
export class SiteStore {
  readonly config = inject(RUNTIME_CONFIG);
  private readonly source = inject(CONTENT_SOURCE);
  private readonly title = inject(Title);
  private readonly meta = inject(Meta);
  readonly data = signal<SiteContent | null>(null);
  readonly error = signal('');
  readonly loading = signal(true);
  private get storageKey() { return `vibix-poc:settings:v2:${this.config.dataMode}:${this.config.siteId}`; }

  async load() {
    this.loading.set(true);
    this.error.set('');
    try {
      const data = await this.source.load(this.config.siteId);
      try {
        const saved = localStorage.getItem(this.storageKey);
        if (saved) {
          const settings: SiteSettings = JSON.parse(saved);
          // Preserve previous local settings saved before templates were introduced.
          if (settings.template === undefined) settings.template = 'bloc';
          if (settings.colorScheme === undefined) settings.colorScheme = 'light';
          if (this.valid(settings)) data.settings = settings;
        }
      } catch { /* Storage may be unavailable; the source settings remain usable. */ }
      this.data.set(data);
      this.apply(data.settings);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : 'Impossible de charger le site. Vous pouvez réessayer.');
    } finally { this.loading.set(false); }
  }

  readDetail(module: ContentModule, id: string) {
    return this.source.detail(this.config.siteId, module, id);
  }

  readCobwebDetail(id: string) { return this.source.cobwebDetail(this.config.siteId, id); }

  valid(value: SiteSettings): boolean {
    return !!value && ['bloc', 'shape'].includes(value.template) && ['light', 'dark'].includes(value.colorScheme) && typeof value.title === 'string' && value.title.trim().length > 0
      && value.title.length <= 200 && typeof value.description === 'string' && value.description.length <= 300
      && (value.logoUrl === null || (typeof value.logoUrl === 'string' && value.logoUrl.length < 3000000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value.logoUrl)))
      && Array.isArray(value.colors?.primary) && value.colors.primary.length === 2
      && Array.isArray(value.colors?.secondary) && value.colors.secondary.length === 2
      && [...value.colors.primary, ...value.colors.secondary].every(color => /^#[0-9a-fA-F]{6}$/.test(color));
  }

  save(settings: SiteSettings): string {
    if (!this.valid(settings)) return 'Choisissez Bloc ou Shape, Light ou Dark, et vérifiez le titre, la description et les couleurs au format #RRGGBB.';
    const current = this.data();
    if (!current) return 'Le site est indisponible.';
    try { localStorage.setItem(this.storageKey, JSON.stringify(settings)); }
    catch { return 'Le stockage local est indisponible ou plein. Les réglages n’ont pas été enregistrés.'; }
    this.data.set({ ...current, settings: structuredClone(settings) });
    this.apply(settings);
    return '';
  }

  async reset(): Promise<string> {
    try {
      const defaults = await this.source.load(this.config.siteId);
      localStorage.removeItem(this.storageKey);
      this.data.set(defaults);
      this.apply(defaults.settings);
      return '';
    } catch { return 'Impossible de réinitialiser les réglages.'; }
  }

  private apply(settings: SiteSettings) {
    document.documentElement.dataset['template'] = settings.template;
    document.documentElement.dataset['colorScheme'] = settings.colorScheme;
    const colors = [...settings.colors.primary, ...settings.colors.secondary];
    ['--primary', '--primary-dark', '--surface', '--background'].forEach((key, index) => {
      document.documentElement.style.setProperty(key, colors[index]);
    });
    this.title.setTitle(settings.title);
    this.meta.updateTag({ name: 'description', content: settings.description });
  }
}
