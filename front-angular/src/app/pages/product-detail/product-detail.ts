import { DOCUMENT } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Title, Meta } from '@angular/platform-browser';
import { combineLatest } from 'rxjs';
import { SiteStore } from '../../site-store';
import { PRODUCT_FILTERS, ProductsClient, ProductDetail, ProductVariant, ProductPage, ProductImage, ProductFeature, ProductTariff, ProductReadError } from '../../products-client';

@Component({ selector: 'app-product-detail', imports: [RouterLink], templateUrl: './product-detail.html' })
export class ProductDetailPage {
  readonly store = inject(SiteStore);
  private readonly client = inject(ProductsClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroy = inject(DestroyRef);
  private readonly title = inject(Title);
  private readonly meta = inject(Meta);
  private readonly document = inject(DOCUMENT);
  readonly item = signal<ProductDetail | null>(null);
  readonly selected = signal<ProductVariant | null>(null);
  readonly variants = signal<ProductPage<ProductVariant> | null>(null);
  readonly gallery = signal<ProductPage<ProductImage> | null>(null);
  readonly variantGallery = signal<ProductPage<ProductImage> | null>(null);
  readonly features = signal<ProductPage<ProductFeature> | null>(null);
  readonly tariffs = signal<ProductPage<ProductTariff> | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly collectionError = signal('');
  readonly busy = signal(false);
  readonly backQuery = signal<Record<string, string>>({});
  private controller = new AbortController();
  private canonical: HTMLLinkElement | null = null;
  private currentId = '';
  private selectedId = '';

  constructor() {
    combineLatest([this.route.paramMap, this.route.queryParamMap]).pipe(takeUntilDestroyed()).subscribe(([params, query]) => {
      const back: Record<string, string> = {};
      for (const key of PRODUCT_FILTERS) if (query.has(key)) back[key] = query.get(key) ?? '';
      this.backQuery.set(back);
      this.currentId = params.get('id') ?? ''; this.selectedId = query.get('variant') ?? '';
      void this.load();
    });
    this.destroy.onDestroy(() => { this.controller.abort(); this.resetSeo(); });
  }

  async load() {
    this.controller.abort(); this.controller = new AbortController();
    const controller = this.controller;
    this.item.set(null); this.selected.set(null); this.variants.set(null); this.gallery.set(null);
    this.variantGallery.set(null); this.features.set(null); this.tariffs.set(null); this.error.set(''); this.collectionError.set('');
    this.loading.set(true); this.busy.set(false); this.resetSeo();
    try {
      if (!this.store.data()?.productsEnabled) throw new Error('Le catalogue Produits est désactivé sur ce site.');
      const item = await this.client.detail(this.currentId, controller.signal);
      const selected = this.selectedId ? await this.client.readVariant(this.currentId, this.selectedId, controller.signal) : null;
      if (controller.signal.aborted) return;
      this.item.set(item); this.gallery.set(item.gallery); this.variants.set(item.variants);
      this.selected.set(selected); this.tariffs.set(selected?.tariffs ?? null); this.features.set(selected?.features ?? null); this.variantGallery.set(selected?.gallery ?? null);
      this.title.setTitle(`${item.seo_title} — ${this.store.data()?.settings.title ?? 'ViBix'}`);
      this.meta.updateTag({ name: 'description', content: item.seo_description }); this.meta.removeTag('name="robots"');
      this.canonical = this.document.createElement('link'); this.canonical.rel = 'canonical';
      this.canonical.href = new URL(`/products/${item.id}`, this.document.location.origin).href;
      this.document.head.append(this.canonical);
      requestAnimationFrame(() => { if (!controller.signal.aborted) this.document.getElementById(selected ? 'variant-title' : 'product-title')?.focus(); });
    } catch (error) {
      if (!controller.signal.aborted) {
        this.error.set(error instanceof Error ? error.message : 'Le produit est indisponible.');
        this.meta.updateTag({ name: 'robots', content: 'noindex, nofollow' });
      }
    } finally { if (!controller.signal.aborted) this.loading.set(false); }
  }

  selectVariant(variantId: number | null) {
    void this.router.navigate(['/products', this.currentId], { queryParams: { ...this.backQuery(), variant: variantId } });
  }

  async loadVariants(page: number) {
    await this.collection(async signal => {
      const data = await this.client.variants(this.currentId, page, signal);
      if (!signal.aborted) this.variants.set(data);
    });
  }
  async loadGallery(page: number) {
    await this.collection(async signal => {
      const data = await this.client.gallery(this.currentId, page, undefined, signal);
      if (!signal.aborted) this.gallery.set(data);
    });
  }
  async loadVariantGallery(page: number) {
    const selected = this.selected(); if (!selected) return;
    await this.collection(async signal => {
      const data = await this.client.gallery(this.currentId, page, String(selected.id), signal);
      if (!signal.aborted) this.variantGallery.set(data);
    });
  }
  async loadFeatures(page: number) {
    const selected = this.selected(); if (!selected) return;
    await this.collection(async signal => {
      const data = await this.client.features(this.currentId, String(selected.id), page, signal);
      if (!signal.aborted) this.features.set(data);
    });
  }

  async loadTariffs(page: number) {
    const selected = this.selected(); if (!selected) return;
    await this.collection(async signal => {
      const data = await this.client.tariffs(this.currentId, String(selected.id), page, signal);
      if (!signal.aborted) this.tariffs.set(data);
    });
  }

  private async collection(read: (signal: AbortSignal) => Promise<void>) {
    const controller = this.controller;
    if (this.busy()) return;
    this.busy.set(true); this.collectionError.set('');
    try { await read(controller.signal); }
    catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ProductReadError && error.status === 404) {
        this.item.set(null); this.selected.set(null); this.resetSeo();
        this.meta.updateTag({ name: 'robots', content: 'noindex, nofollow' }); this.error.set(error.message);
      } else this.collectionError.set(error instanceof Error ? error.message : 'La collection est indisponible.');
    } finally { if (!controller.signal.aborted) this.busy.set(false); }
  }

  featureLabel(feature: ProductFeature) { return feature.kind === 'boolean' ? (feature.value === 'true' ? 'Oui' : 'Non') : feature.value; }
  private resetSeo() {
    this.canonical?.remove(); this.canonical = null; this.meta.removeTag('name="robots"');
    const settings = this.store.data()?.settings;
    if (settings) { this.title.setTitle(settings.title); this.meta.updateTag({ name: 'description', content: settings.description }); }
  }
}
