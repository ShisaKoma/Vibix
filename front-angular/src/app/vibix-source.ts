import { DEFAULT_CLIENT_OPTIONS } from './client-options';
import { ClientOptions, SettingOption, ContentAsset, CobwebGroup, CobwebItem, ContentItem, ContentModule, ContentSource, NavigationNode, RuntimeConfig, SiteContent, SiteSettings } from './content';

interface PublicSite { articles_enabled?: boolean; pages_enabled?: boolean; cobweb_enabled?: boolean; products_enabled?: boolean; planning_enabled?: boolean; contacts_enabled?: boolean; frontend_options?: {templates: SettingOption[]; color_schemes: SettingOption[]}; id: number; name: string; description: string; colors: SiteSettings['colors']; }
interface PublicContent {
  id: number; title: string; description: string; content_html: string;
  picture?: PublicAsset | null; thumbnail?: PublicAsset | null;
  category_id: number; position: number; group_id?: number | null; in_home?: boolean;
  gallery?: {position: number; asset: PublicAsset}[];
}
interface PublicNavigation { id: number; parent_id: number | null; label: string; position: number; field: string; }
interface PublicContentPage { count: number; page: number; pages: number; results: PublicContent[]; navigation: PublicNavigation[]; }

interface PublicAsset { id: number; url: string; alt_text: string; width: number; height: number; }
interface PublicCobwebItem { id: number; title: string; description: string; content_html: string; kind: string | null; link: string; picture: PublicAsset | null; thumbnail: PublicAsset | null; category_id?: number; position?: number; }
interface PublicCobwebGroup { id: number; title: string; content_html: string; position: number; presentation: 'cards' | 'list' | 'slideshow'; members_url: string; }
interface PublicPage<T> { count: number; page: number; pages: number; results: T[]; }

export class VibixContentSource implements ContentSource {
  constructor(private readonly config: RuntimeConfig) {}

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.config.apiBase}${path}`, {
        credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(15000),
        headers: { Accept: 'application/json' }
      });
    } catch { throw new Error('L’API ViBix est inaccessible. Vérifiez sa disponibilité et réessayez.'); }
    if (response.status === 404) throw new Error('Ce site ou ce contenu n’est pas disponible dans l’API publique.');
    if (!response.ok) throw new Error(`L’API ViBix a refusé la lecture (HTTP ${response.status}).`);
    try { return await response.json() as T; }
    catch { throw new Error('La réponse de ViBix n’est pas un document JSON valide.'); }
  }

  async load(siteId: number): Promise<SiteContent> {
    const prefix = `/sites/${siteId}/`;
    const site = await this.get<PublicSite>(prefix);
    const [articles, pages, cobwebGroups, cobwebItems] = await Promise.all([
      site.articles_enabled === false ? Promise.resolve({items: [], navigation: []}) : this.collection(prefix, 'articles', siteId),
      site.pages_enabled === false ? Promise.resolve({items: [], navigation: []}) : this.collection(prefix, 'pages', siteId),
      site.cobweb_enabled === false ? Promise.resolve([]) : this.groups(siteId),
      site.cobweb_enabled === false ? Promise.resolve([]) : this.rows<PublicCobwebItem>(`${prefix}cobweb/`).then(rows => rows.map(row => this.cobwebItem(row, siteId)))
    ]);
    if (site.id !== siteId || typeof site.name !== 'string' || typeof site.description !== 'string'
      || !Array.isArray(site.colors?.primary) || site.colors.primary.length !== 2
      || !Array.isArray(site.colors?.secondary) || site.colors.secondary.length !== 2
      || ![...site.colors.primary, ...site.colors.secondary].every(color => /^#[0-9a-fA-F]{6}$/.test(color))) {
      throw new Error('L’identité retournée ne respecte pas le contrat public ViBix.');
    }
    return {
      contactsEnabled: site.contacts_enabled === true,
      planningEnabled: site.planning_enabled === true,
      productsEnabled: site.products_enabled === true,
      articlesEnabled: site.articles_enabled !== false,
      pagesEnabled: site.pages_enabled !== false,
      cobwebEnabled: site.cobweb_enabled !== false,
      settings: { title: site.name, description: site.description, colors: site.colors, logoUrl: null, template: 'bloc', colorScheme: 'light' },
      clientOptions: this.options(site),
      navigation: { articles: articles.navigation, pages: pages.navigation },
      articles: articles.items, pages: pages.items, cobwebGroups, cobwebItems
    };
  }

  private options(site: PublicSite): ClientOptions {
    if (site.frontend_options === undefined) return structuredClone(DEFAULT_CLIENT_OPTIONS);
    const valid = (rows: SettingOption[]) => Array.isArray(rows) && rows.length <= 100
      && rows.every(row => row && typeof row.value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(row.value)
        && typeof row.label === 'string' && row.label.length <= 100)
      && new Set(rows.map(row => row.value)).size === rows.length;
    if (!valid(site.frontend_options?.templates) || !valid(site.frontend_options?.color_schemes)) {
      throw new Error('Les choix du front ne respectent pas le contrat ViBix.');
    }
    return { templates: site.frontend_options.templates, colorSchemes: site.frontend_options.color_schemes };
  }

  private async collection(prefix: string, module: ContentModule, siteId: number) {
    const first = await this.get<PublicContentPage>(`${prefix}${module}/`);
    this.validatePage(first, 1);
    // The small POC aggregates pages to apply category/group descendant filters locally.
    // Refuse oversized collections explicitly rather than silently truncating them.
    if (first.pages > 100) throw new Error('Ce site dépasse la limite du POC (100 pages API par module).');
    const items = first.results.map(item => this.item(item, siteId));
    for (let page = 2; page <= first.pages; page++) {
      const next = await this.get<PublicContentPage>(`${prefix}${module}/?page=${page}`);
      this.validatePage(next, page);
      items.push(...next.results.map(item => this.item(item, siteId)));
    }
    const navigation: NavigationNode[] = first.navigation.map(node => {
      if (!Number.isSafeInteger(node.id) || (node.parent_id !== null && !Number.isSafeInteger(node.parent_id))
        || typeof node.label !== 'string' || !Number.isSafeInteger(node.position)
        || ![`${module}_${module === 'articles' ? 'article' : 'page'}.category`, 'pages_page.group'].includes(node.field)) {
        throw new Error('La navigation retournée ne respecte pas le contrat ViBix.');
      }
      const field = node.field.endsWith('.group') ? 'group' : 'category';
      return { id: `${field}:${node.id}`, parentId: node.parent_id === null ? null : `${field}:${node.parent_id}`,
        label: node.label, position: node.position, field };
    });
    return { items, navigation };
  }

  private validatePage(data: PublicContentPage, expected: number) {
    if (!data || data.page !== expected || !Number.isSafeInteger(data.pages) || data.pages < 1
      || !Number.isSafeInteger(data.count) || data.count < 0 || !Array.isArray(data.results)
      || data.results.length > 20 || !Array.isArray(data.navigation)) {
      throw new Error('La collection retournée ne respecte pas le contrat ViBix.');
    }
  }

  private item(data: PublicContent, siteId: number): ContentItem {
    if (!data || !Number.isSafeInteger(data.id) || data.id < 1 || typeof data.title !== 'string'
      || typeof data.description !== 'string' || typeof data.content_html !== 'string'
      || !Number.isSafeInteger(data.category_id) || !Number.isSafeInteger(data.position)
      || (data.group_id != null && !Number.isSafeInteger(data.group_id))) {
      throw new Error('Le contenu retourné ne respecte pas le contrat ViBix.');
    }
    return { id: String(data.id), title: data.title, description: data.description,
      contentHtml: data.content_html, categoryId: `category:${data.category_id}`,
      groupId: data.group_id == null ? undefined : `group:${data.group_id}`,
      picture: this.asset(data.picture, siteId), thumbnail: this.asset(data.thumbnail, siteId),
      position: data.position, inHome: data.in_home ?? false,
      gallery: this.gallery(data.gallery, siteId) };
  }

  private gallery(rows: PublicContent['gallery'], siteId: number): ContentItem['gallery'] {
    if (rows === undefined) return [];
    if (!Array.isArray(rows) || rows.length > 100) throw new Error('La galerie ne respecte pas le contrat ViBix.');
    return rows.map(row => {
      if (!row || !Number.isSafeInteger(row.position) || row.position < 0) throw new Error('Ordre de galerie invalide.');
      const asset = this.asset(row.asset, siteId);
      if (!asset) throw new Error('Image de galerie invalide.');
      return { position: row.position, asset };
    }).sort((a, b) => a.position - b.position);
  }


  private asset(data: PublicAsset | null | undefined, siteId: number): ContentAsset | null {
    if (data == null) return null;
    if (!Number.isSafeInteger(data.id) || data.id < 1 || typeof data.alt_text !== 'string'
      || !Number.isSafeInteger(data.width) || data.width < 1 || !Number.isSafeInteger(data.height) || data.height < 1
      || data.url !== `${this.config.apiBase}/sites/${siteId}/media/${data.id}/image/`) {
      throw new Error('Le média retourné ne respecte pas le périmètre public du site.');
    }
    return { id: data.id, url: data.url, altText: data.alt_text, width: data.width, height: data.height };
  }

  private async rows<T>(path: string): Promise<T[]> {
    const results: T[] = [];
    let pages = 1;
    for (let page = 1; page <= pages; page++) {
      const response = await this.get<PublicPage<T>>(`${path}${path.includes('?') ? '&' : '?'}page=${page}`);
      if (!response || response.page !== page || !Number.isSafeInteger(response.pages) || response.pages < 1
        || !Number.isSafeInteger(response.count) || response.count < 0 || !Array.isArray(response.results)
        || response.results.length > 20) throw new Error('La collection Cobweb ne respecte pas le contrat.');
      if (page === 1) {
        pages = response.pages;
        if (pages > 100) throw new Error('La composition dépasse la limite du POC (100 pages API).');
      }
      results.push(...response.results);
    }
    return results;
  }

  private cobwebItem(data: PublicCobwebItem, siteId: number): CobwebItem {
    if (!data || !Number.isSafeInteger(data.id) || data.id < 1 || typeof data.title !== 'string'
      || typeof data.description !== 'string' || typeof data.content_html !== 'string'
      || !(data.kind === null || typeof data.kind === 'string') || typeof data.link !== 'string'
      || (data.category_id !== undefined && (!Number.isSafeInteger(data.category_id) || data.category_id < 1))
      || (data.position !== undefined && (!Number.isSafeInteger(data.position) || data.position < 0))) {
      throw new Error('Une entrée Cobweb ne respecte pas le contrat.');
    }
    if (data.link && !/^https?:\/\//i.test(data.link)) throw new Error('Le lien Cobweb doit être HTTP(S).');
    return { id: String(data.id), title: data.title, description: data.description,
      contentHtml: data.content_html, kind: data.kind, link: data.link,
      picture: this.asset(data.picture, siteId), thumbnail: this.asset(data.thumbnail, siteId),
      categoryId: data.category_id === undefined ? undefined : `category:${data.category_id}`,
      position: data.position };
  }

  private async groups(siteId: number): Promise<CobwebGroup[]> {
    const path = `/sites/${siteId}/cobweb/groups/`;
    const [groups, partners] = await Promise.all([
      this.rows<PublicCobwebGroup>(path), this.rows<PublicCobwebGroup>(`${path}?category_code=partners`)
    ]);
    const partnerIds = new Set(partners.map(group => group.id));
    const result: CobwebGroup[] = [];
    // Bound concurrent membership requests; a large collection never opens thousands at once.
    for (let start = 0; start < groups.length; start += 4) {
      result.push(...await Promise.all(groups.slice(start, start + 4).map(async group => {
        if (!Number.isSafeInteger(group.id) || group.id < 1 || typeof group.title !== 'string'
          || typeof group.content_html !== 'string' || !Number.isSafeInteger(group.position)
          || !['cards', 'list', 'slideshow'].includes(group.presentation)
          || group.members_url !== `${this.config.apiBase}${path}${group.id}/members/`) {
          throw new Error('Une composition Cobweb ne respecte pas le contrat.');
        }
        const members = await this.rows<{position: number; item: PublicCobwebItem}>(`${path}${group.id}/members/`);
        return { id: String(group.id), title: group.title, contentHtml: group.content_html,
          position: group.position, presentation: group.presentation, partners: partnerIds.has(group.id),
          members: members.map(member => {
            if (!Number.isSafeInteger(member.position) || member.position < 0) throw new Error('Ordre de membre Cobweb invalide.');
            return { position: member.position, item: this.cobwebItem(member.item, siteId) };
          }).sort((a, b) => a.position - b.position)
        };
      })));
    }
    return result.sort((a, b) => a.position - b.position);
  }

  async detail(siteId: number, module: ContentModule, id: string): Promise<ContentItem | null> {
    if (!/^[1-9]\d*$/.test(id)) return null;
    return this.item(await this.get<PublicContent>(`/sites/${siteId}/${module}/${id}/`), siteId);
  }

  async cobwebDetail(siteId: number, id: string): Promise<CobwebItem | null> {
    if (!/^[1-9]\d*$/.test(id)) return null;
    return this.cobwebItem(await this.get<PublicCobwebItem>(`/sites/${siteId}/cobweb/${id}/`), siteId);
  }
}
