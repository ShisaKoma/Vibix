import { InjectionToken } from '@angular/core';

export interface RuntimeConfig { siteId: number; dataMode: 'mock' | 'api'; apiBase: string; }
export const RUNTIME_CONFIG = new InjectionToken<RuntimeConfig>('Runtime configuration');
export type SiteTemplate = 'bloc' | 'shape' | 'custom';
export type { ClientOptions, SettingOption } from './client-options';
import type { ClientOptions } from './client-options';
export interface SiteSettings {
  colorScheme: 'light' | 'dark';
  template: SiteTemplate;
  title: string; description: string; logoUrl: string | null;
  colors: { primary: [string, string]; secondary: [string, string] };
}
export interface NavigationNode { id: string; parentId: string | null; label: string; position: number; field?: 'category' | 'group'; }
export type ContentModule = 'articles' | 'pages';
export interface ContentItem {
  id: string; title: string; description: string; contentHtml: string;
  picture?: ContentAsset | null; thumbnail?: ContentAsset | null;
  gallery?: { position: number; asset: ContentAsset }[];
  categoryId: string; groupId?: string; position: number; inHome?: boolean;
}
export interface ContentAsset { id: number; url: string; altText: string; width: number; height: number; }
export interface CobwebItem {
  id: string; title: string; description: string; contentHtml: string; kind: string | null;
  link: string; picture: ContentAsset | null; thumbnail: ContentAsset | null;
  categoryId?: string; position?: number;
}
export interface CobwebGroup {
  id: string; title: string; contentHtml: string; presentation: 'cards' | 'list' | 'slideshow';
  position: number; partners: boolean; members: { position: number; item: CobwebItem }[];
}
export interface SiteContent {
  contactsEnabled?: boolean;
  planningEnabled?: boolean;
  productsEnabled?: boolean;
  articlesEnabled?: boolean;
  pagesEnabled?: boolean;
  cobwebEnabled?: boolean;
  settings: SiteSettings;
  clientOptions: ClientOptions;
  navigation: { articles: NavigationNode[]; pages: NavigationNode[] };
  articles: ContentItem[]; pages: ContentItem[]; cobwebGroups: CobwebGroup[]; cobwebItems: CobwebItem[];
}
// Internal display model, mapped from the public ViBix OpenAPI contract.
export interface ContentSource { load(siteId: number): Promise<SiteContent>;
  detail(siteId: number, module: ContentModule, id: string): Promise<ContentItem | null>;
  cobwebDetail(siteId: number, id: string): Promise<CobwebItem | null>; }
export const CONTENT_SOURCE = new InjectionToken<ContentSource>('Content source');
