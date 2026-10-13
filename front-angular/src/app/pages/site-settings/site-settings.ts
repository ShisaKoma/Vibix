import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SiteStore } from '../../site-store';

@Component({
  selector: 'app-settings', imports: [FormsModule],
  templateUrl: './site-settings.html'
})
export class SettingsView {
  readonly store = inject(SiteStore);
  draft = structuredClone(this.store.data()!.settings);
  readonly message = signal('');
  readonly options = this.store.data()!.clientOptions;
  readonly supportedTemplates = ['bloc', 'shape'];
  readonly supportedSchemes = ['light', 'dark'];
  readonly uploading = signal(false);
  readonly colorFields = [
    { index: 0, label: 'Principale · accents', group: 'primary', slot: 0 },
    { index: 1, label: 'Principale · titres', group: 'primary', slot: 1 },
    { index: 2, label: 'Secondaire · surfaces', group: 'secondary', slot: 0 },
    { index: 3, label: 'Secondaire · fond', group: 'secondary', slot: 1 }
  ] as const;

  save() {
    this.message.set(this.store.save(this.draft) || 'Réglages enregistrés. L’accueil et le template sont à jour.');
  }

  async reset() {
    const error = await this.store.reset();
    if (error) { this.message.set(error); return; }
    this.draft = structuredClone(this.store.data()!.settings);
    this.message.set('Les réglages de la source ont été restaurés.');
  }

  async upload(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) {
      this.message.set('Choisissez une image PNG, JPEG ou WebP de 2 Mo maximum.'); input.value = ''; return;
    }
    this.uploading.set(true);
    try {
      const image = await createImageBitmap(file);
      // Store a small re-encoded PNG, not the original upload.
      const ratio = Math.min(1, 512 / Math.max(image.width, image.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(image.width * ratio)); canvas.height = Math.max(1, Math.round(image.height * ratio));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas unavailable');
      context.drawImage(image, 0, 0, canvas.width, canvas.height); image.close();
      this.draft.logoUrl = canvas.toDataURL('image/png');
      this.message.set('Logo chargé. Enregistrez les réglages pour l’appliquer.');
    } catch { this.message.set('Cette image ne peut pas être décodée.'); }
    finally { this.uploading.set(false); input.value = ''; }
  }
}
