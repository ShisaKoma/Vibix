import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Title, Meta } from '@angular/platform-browser';
import { SiteStore } from '../../site-store';
import { PRODUCT_FILTERS, ProductsClient, ProductBrand, ProductCategory, ProductPage, ProductSummary } from '../../products-client';

@Component({ selector: 'app-products', imports: [FormsModule, RouterLink], templateUrl: './products.html' })
export class ProductsPage {
  readonly store = inject(SiteStore);
  private readonly client = inject(ProductsClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroy = inject(DestroyRef);
  private readonly title = inject(Title);
  private readonly meta = inject(Meta);
  readonly data = signal<ProductPage<ProductSummary> | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly referencesError = signal('');
  readonly categories = signal<(ProductCategory & { depth: number })[]>([]);
  readonly brands = signal<ProductBrand[]>([]);
  readonly query = signal<Record<string, string>>({});
  filters: Record<string, string> = {};
  private controller = new AbortController();
  private readonly referencesController = new AbortController();

  constructor() {
    this.title.setTitle(`Produits — ${this.store.data()?.settings.title ?? 'ViBix'}`);
    this.meta.updateTag({ name: 'description', content: 'Catalogue des produits publiés.' });
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe(params => {
      const query: Record<string, string> = {};
      for (const key of PRODUCT_FILTERS) {
        if (params.has(key)) query[key] = params.get(key) ?? '';
      }
      this.query.set(query); this.filters = Object.fromEntries(PRODUCT_FILTERS.map(key => [key, query[key] || ''])); void this.load();
    });
    void this.loadReferences();
    this.destroy.onDestroy(() => {
      this.controller.abort(); this.referencesController.abort();
      const settings = this.store.data()?.settings;
      if (settings) { this.title.setTitle(settings.title); this.meta.updateTag({ name: 'description', content: settings.description }); }
    });
  }

  async load() {
    this.controller.abort(); this.controller = new AbortController();
    const controller = this.controller;
    this.data.set(null); this.error.set(''); this.loading.set(true);
    try {
      if (!this.store.data()?.productsEnabled) throw new Error('Le catalogue Produits est désactivé sur ce site.');
      const data = await this.client.catalog(this.query(), controller.signal);
      if (!controller.signal.aborted) this.data.set(data);
    } catch (error) {
      if (!controller.signal.aborted) this.error.set(error instanceof Error ? error.message : 'Le catalogue est indisponible.');
    } finally { if (!controller.signal.aborted) this.loading.set(false); }
  }

  async loadReferences() {
    if (!this.store.data()?.productsEnabled) return;
    this.referencesError.set(''); this.categories.set([]); this.brands.set([]);
    try {
      const [categories, brands] = await Promise.all([this.client.categories(this.referencesController.signal), this.client.brands(this.referencesController.signal)]);
      if (this.referencesController.signal.aborted) return;
      const rows: (ProductCategory & { depth: number })[] = [];
      const walk = (parent: number | null, depth: number) => {
        for (const node of categories.filter(row => row.parent_id === parent)) {
          rows.push({ ...node, depth }); walk(node.id, depth + 1);
        }
      };
      walk(null, 0); this.categories.set(rows); this.brands.set(brands);
    } catch (error) {
      if (!this.referencesController.signal.aborted) this.referencesError.set(error instanceof Error ? error.message : 'Les filtres sont indisponibles.');
    }
  }

  apply() { void this.router.navigate(['/products'], { queryParams: { ...Object.fromEntries(Object.entries(this.filters).filter(([, value]) => value)), page: 1 } }); }
  reset() { void this.router.navigate(['/products']); }
  categoryLabel(category: ProductCategory & { depth: number }) { return `${'— '.repeat(Math.min(category.depth, 10))}${category.label} (${category.count})`; }
}
