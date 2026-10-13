import { Injectable, inject } from '@angular/core';
import { RUNTIME_CONFIG } from './content';

export interface ProductAsset { id: number; url: string; alt_text: string; width: number; height: number; }
export interface ProductPage<T> { count: number; page: number; pages: number; results: T[]; }
export interface ProductCategory { id: number; parent_id: number | null; code: string; label: string; position: number; count: number; }
export interface ProductBrand { id: number; code: string; name: string; description: string; }
export interface ProductImage { id: number; position: number; asset: ProductAsset; }
export interface ProductFeature { id: number; code: string; label: string; kind: 'text' | 'number' | 'date' | 'boolean' | 'choice'; unit: string; value: string; }
export interface ProductTariff {
  id: number; label: string; unit: string; quantity: string; currency: string; basis: 'HT' | 'TTC';
  on_request: boolean; amount: string | null; normal_amount: string | null; tax_rate: string | null;
  amounts: { net: string | null; gross: string | null }; unit_amount: string | null; terms: string;
  promotion: { kind: 'amount' | 'percent'; value: string; starts_at: string; ends_at: string } | null;
}
export interface ProductSummary {
  id: number; reference: string; title: string; subtitle: string; summary: string;
  category_id: number; category_label: string; language: string;
  brand: { id: number; name: string } | null; position: number;
  featured: boolean; new: boolean; in_home: boolean; reservable: boolean;
  reservation_status: 'configuration_required' | 'internal_only' | 'not_reservable';
  picture: ProductAsset | null; thumbnail: ProductAsset | null;
}
export interface ProductVariant {
  id: number; reference: string; title: string; content_html: string; position: number;
  content_inherited: boolean; picture_inherited: boolean; picture: ProductAsset | null;
  dimensions: { length: string | null; width: string | null; height: string | null; weight: string | null; dimension_unit: string; weight_unit: string };
  features: ProductPage<ProductFeature>; features_url: string;
  gallery: ProductPage<ProductImage>; gallery_url: string;
  tariffs: ProductPage<ProductTariff>; tariffs_url: string;
}
export interface ProductDetail extends ProductSummary {
  content_html: string; terms: string; slug: string; seo_title: string; seo_description: string;
  additional_category_ids: number[];
  gallery: ProductPage<ProductImage>; gallery_url: string;
  variants: ProductPage<ProductVariant>; variants_url: string;
}
export const PRODUCT_FILTERS = ['q', 'category_id', 'brand_id', 'language', 'featured', 'new', 'in_home', 'reservable', 'order', 'page'] as const;
export class ProductReadError extends Error { constructor(message: string, readonly status = 0) { super(message); } }
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const id = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown, max = 50000): value is string => typeof value === 'string' && value.length <= max;
const decimal = (value: unknown) => value === null || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value) && value.length <= 20);

@Injectable({ providedIn: 'root' })
export class ProductsClient {
  private readonly config = inject(RUNTIME_CONFIG);
  private get base() { return `${this.config.apiBase}/sites/${this.config.siteId}/products/`; }

  filters(query: Record<string, string>): URLSearchParams {
    const params = new URLSearchParams();
    for (const key of PRODUCT_FILTERS) {
      const value = query[key];
      if (!value) continue;
      if ((key === 'q' && value.length > 200)
        || (['category_id', 'brand_id', 'page'].includes(key) && (!/^\d+$/.test(value) || !id(Number(value))))
        || (key === 'language' && !/^[A-Za-z0-9_-]{1,64}$/.test(value))
        || (['featured', 'new', 'in_home', 'reservable'].includes(key) && !['true', 'false'].includes(value))
        || (key === 'order' && !['order', 'title', '-title', 'created_at', '-created_at'].includes(value))) {
        throw new ProductReadError('Les filtres de cette adresse sont invalides. Réinitialisez la recherche.', 400);
      }
      params.set(key, value);
    }
    return params;
  }

  async catalog(query: Record<string, string>, signal?: AbortSignal): Promise<ProductPage<ProductSummary>> {
    const params = this.filters(query);
    const page = Number(params.get('page') || '1');
    const data = await this.get(`?${params}`, signal);
    return this.page(data, page, value => this.summary(value), 20);
  }

  async detail(productId: string, signal?: AbortSignal): Promise<ProductDetail> {
    this.identifier(productId);
    const value = await this.get(`${productId}/`, signal);
    const summary = this.summary(value);
    if (!record(value) || String(summary.id) !== productId || !text(value['content_html'])
      || !text(value['terms'], 10000) || !text(value['slug'], 200) || !text(value['seo_title'], 200)
      || !text(value['seo_description'], 2000) || !Array.isArray(value['additional_category_ids'])
      || value['additional_category_ids'].length > 200 || !value['additional_category_ids'].every(id)
      || value['gallery_url'] !== `${this.base}${productId}/gallery/`
      || value['variants_url'] !== `${this.base}${productId}/variants/`) this.invalid();
    return { ...summary, content_html: value['content_html'] as string, terms: value['terms'] as string,
      slug: value['slug'] as string, seo_title: value['seo_title'] as string, seo_description: value['seo_description'] as string,
      additional_category_ids: value['additional_category_ids'] as number[],
      gallery: this.page(value['gallery'], 1, row => this.image(row), 20), gallery_url: value['gallery_url'] as string,
      variants: this.page(value['variants'], 1, row => this.variant(row, productId), 20), variants_url: value['variants_url'] as string };
  }

  async variants(productId: string, page: number, signal?: AbortSignal): Promise<ProductPage<ProductVariant>> {
    this.identifier(productId);
    return this.page(await this.get(`${productId}/variants/?page=${page}`, signal), page, row => this.variant(row, productId), 20);
  }

  async readVariant(productId: string, variantId: string, signal?: AbortSignal): Promise<ProductVariant> {
    this.identifier(productId); this.identifier(variantId);
    const variant = this.variant(await this.get(`${productId}/variants/${variantId}/`, signal), productId);
    if (String(variant.id) !== variantId) this.invalid();
    return variant;
  }

  async gallery(productId: string, page: number, variantId?: string, signal?: AbortSignal): Promise<ProductPage<ProductImage>> {
    this.identifier(productId);
    if (variantId) this.identifier(variantId);
    const path = variantId ? `${productId}/variants/${variantId}/gallery/` : `${productId}/gallery/`;
    return this.page(await this.get(`${path}?page=${page}`, signal), page, row => this.image(row), 20);
  }

  async features(productId: string, variantId: string, page: number, signal?: AbortSignal): Promise<ProductPage<ProductFeature>> {
    this.identifier(productId); this.identifier(variantId);
    return this.page(await this.get(`${productId}/variants/${variantId}/features/?page=${page}`, signal), page, row => this.feature(row), 20);
  }

  async tariffs(productId: string, variantId: string, page: number, signal?: AbortSignal): Promise<ProductPage<ProductTariff>> {
    this.identifier(productId); this.identifier(variantId);
    return this.page(await this.get(`${productId}/variants/${variantId}/tariffs/?page=${page}`, signal), page, row => this.tariff(row), 20);
  }

  async categories(signal?: AbortSignal): Promise<ProductCategory[]> {
    const rows = await this.references('categories', value => {
      if (!record(value) || !id(value['id']) || !(value['parent_id'] === null || id(value['parent_id']))
        || !text(value['code'], 64) || !text(value['label'], 100) || !integer(value['position']) || !id(value['count'])) this.invalid();
      return value as unknown as ProductCategory;
    }, signal);
    const nodes = new Map(rows.map(row => [row.id, row]));
    for (const row of rows) {
      const seen = new Set<number>(); let parent: number | null = row.id;
      while (parent !== null) {
        if (seen.has(parent) || !nodes.has(parent)) this.invalid();
        seen.add(parent); parent = nodes.get(parent)!.parent_id;
      }
    }
    return rows;
  }

  brands(signal?: AbortSignal): Promise<ProductBrand[]> {
    return this.references('brands', value => {
      if (!record(value) || !id(value['id']) || !text(value['code'], 64)
        || !text(value['name'], 120) || !text(value['description'], 5000)) this.invalid();
      return value as unknown as ProductBrand;
    }, signal);
  }

  private async references<T extends { id: number }>(path: string, map: (value: unknown) => T, signal?: AbortSignal): Promise<T[]> {
    const first = this.page(await this.get(`${path}/?page_size=100`, signal), 1, map, 100);
    if (first.pages > 100) throw new ProductReadError('Les référentiels dépassent la limite de ce front.');
    const rows = [...first.results];
    for (let page = 2; page <= first.pages; page++) {
      const next = this.page(await this.get(`${path}/?page_size=100&page=${page}`, signal), page, map, 100);
      if (next.count !== first.count || next.pages !== first.pages) throw new ProductReadError('Le catalogue a changé pendant le chargement des filtres. Réessayez.');
      rows.push(...next.results);
    }
    if (rows.length !== first.count || new Set(rows.map(row => row.id)).size !== rows.length) this.invalid();
    return rows;
  }

  private async get(path: string, signal?: AbortSignal): Promise<unknown> {
    if (this.config.dataMode !== 'api') throw new ProductReadError('Le catalogue Produits est disponible en mode API.');
    let response: Response;
    try { response = await fetch(`${this.base}${path}`, {
      credentials: 'omit', cache: 'no-store', headers: { Accept: 'application/json' },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    }); } catch (error) {
      if (signal?.aborted) throw error;
      throw new ProductReadError('Le catalogue est inaccessible. Réessayez.');
    }
    if (response.status === 404) throw new ProductReadError('Ce catalogue, produit ou cette déclinaison a été retiré ou n’est pas disponible.', 404);
    if (response.status === 400) throw new ProductReadError('La recherche ou la page demandée est invalide. Revenez à la première page.', 400);
    if (!response.ok) throw new ProductReadError(`Lecture du catalogue refusée (HTTP ${response.status}).`, response.status);
    try { return await response.json(); } catch { this.invalid(); }
  }

  private page<T>(value: unknown, expected: number, map: (value: unknown) => T, size: number): ProductPage<T> {
    if (!record(value) || value['page'] !== expected || !id(value['pages']) || expected > value['pages']
      || !integer(value['count']) || !Array.isArray(value['results']) || value['results'].length > size
      || value['pages'] !== Math.max(1, Math.ceil(value['count'] / size))) this.invalid();
    return { count: value['count'] as number, page: expected, pages: value['pages'] as number,
      results: (value['results'] as unknown[]).map(map) };
  }

  private asset(value: unknown): ProductAsset | null {
    if (value === null) return null;
    if (!record(value) || !id(value['id']) || !id(value['width']) || !id(value['height'])
      || !text(value['alt_text'], 300) || value['url'] !== `${this.config.apiBase}/sites/${this.config.siteId}/media/${value['id']}/image/`) this.invalid();
    return value as unknown as ProductAsset;
  }

  private summary(value: unknown): ProductSummary {
    if (!record(value) || !id(value['id']) || !text(value['reference'], 100) || !text(value['title'], 200)
      || !text(value['subtitle'], 300) || !text(value['summary'], 2000) || !id(value['category_id'])
      || !text(value['category_label'], 100) || !text(value['language'], 64) || !integer(value['position'])
      || !['featured', 'new', 'in_home', 'reservable'].every(key => typeof value[key] === 'boolean')
      || !(value['reservable'] ? ['configuration_required', 'internal_only'].includes(value['reservation_status'] as string) : value['reservation_status'] === 'not_reservable')) this.invalid();
    const brand = value['brand'];
    if (!(brand === null || (record(brand) && id(brand['id']) && text(brand['name'], 120)))) this.invalid();
    return { ...value, picture: this.asset(value['picture']), thumbnail: this.asset(value['thumbnail']) } as unknown as ProductSummary;
  }

  private variant(value: unknown, productId: string): ProductVariant {
    if (!record(value) || !id(value['id']) || !text(value['reference'], 100) || !text(value['title'], 200)
      || !text(value['content_html']) || !integer(value['position']) || typeof value['content_inherited'] !== 'boolean'
      || typeof value['picture_inherited'] !== 'boolean' || !record(value['dimensions'])) this.invalid();
    const dimensions = value['dimensions'] as Record<string, unknown>;
    if (!['length', 'width', 'height', 'weight'].every(key => decimal(dimensions[key]))
      || !['', 'mm', 'cm', 'm'].includes(dimensions['dimension_unit'] as string)
      || !['', 'g', 'kg'].includes(dimensions['weight_unit'] as string)
      || value['features_url'] !== `${this.base}${productId}/variants/${value['id']}/features/`
      || value['gallery_url'] !== `${this.base}${productId}/variants/${value['id']}/gallery/`
      || value['tariffs_url'] !== `${this.base}${productId}/variants/${value['id']}/tariffs/`) this.invalid();
    return { ...value, picture: this.asset(value['picture']),
      features: this.page(value['features'], 1, row => this.feature(row), 20),
      gallery: this.page(value['gallery'], 1, row => this.image(row), 20),
      tariffs: this.page(value['tariffs'], 1, row => this.tariff(row), 20) } as unknown as ProductVariant;
  }

  private image(value: unknown): ProductImage {
    if (!record(value) || !id(value['id']) || !integer(value['position'])) this.invalid();
    const asset = this.asset(value['asset']); if (!asset) this.invalid();
    return { id: value['id'] as number, position: value['position'] as number, asset };
  }

  private feature(value: unknown): ProductFeature {
    if (!record(value) || !id(value['id']) || !text(value['code'], 64) || !text(value['label'], 120)
      || !text(value['unit'], 30) || !text(value['value'], 2000)
      || !['text', 'number', 'date', 'boolean', 'choice'].includes(value['kind'] as string)) this.invalid();
    return value as unknown as ProductFeature;
  }

  private tariff(value: unknown): ProductTariff {
    const price = (row: unknown) => row === null || (typeof row === 'string' && /^\d{1,18}\.\d{2}$/.test(row));
    const numeric = (row: unknown) => typeof row === 'string' && /^\d+(?:\.\d+)?$/.test(row) && row.length <= 20;
    if (!record(value) || !id(value['id']) || !text(value['label'], 120) || !text(value['unit'], 64)
      || !numeric(value['quantity']) || Number(value['quantity']) <= 0 || typeof value['currency'] !== 'string'
      || !/^[A-Z]{3}$/.test(value['currency']) || !['HT', 'TTC'].includes(value['basis'] as string)
      || typeof value['on_request'] !== 'boolean' || !price(value['amount']) || !price(value['normal_amount'])
      || !price(value['unit_amount']) || !text(value['terms'], 5000) || !record(value['amounts'])
      || !price(value['amounts']['net']) || !price(value['amounts']['gross'])
      || !(value['tax_rate'] === null || numeric(value['tax_rate']))
      || value['on_request'] !== (value['amount'] === null)) this.invalid();
    const promo = value['promotion'];
    if (!(promo === null || (record(promo) && ['amount', 'percent'].includes(promo['kind'] as string)
      && numeric(promo['value']) && typeof promo['starts_at'] === 'string' && typeof promo['ends_at'] === 'string'
      && Number.isFinite(Date.parse(promo['starts_at'])) && Number.isFinite(Date.parse(promo['ends_at']))))) this.invalid();
    return value as unknown as ProductTariff;
  }

  private identifier(value: string) {
    if (!/^[1-9]\d*$/.test(value) || !id(Number(value))) throw new ProductReadError('Cette référence de produit est invalide.', 404);
  }
  private invalid(): never { throw new ProductReadError('La réponse ne respecte pas le contrat public Produits.'); }
}
