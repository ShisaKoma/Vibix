import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter, withInMemoryScrolling, Routes } from '@angular/router';
import { App } from './app/app';
import { CONTENT_SOURCE, RUNTIME_CONFIG, RuntimeConfig } from './app/content';
import { MockContentSource } from './app/mock-source';
import { VibixContentSource } from './app/vibix-source';

const routes: Routes = [
  { path: 'products', loadComponent: () => import('./app/pages/products/products').then(m => m.ProductsPage) },
  { path: 'products/:id', loadComponent: () => import('./app/pages/product-detail/product-detail').then(m => m.ProductDetailPage) },
  { path: 'events', loadComponent: () => import('./app/pages/events/events').then(m => m.EventsPage) },
  { path: '', loadComponent: () => import('./app/pages/home/home').then(m => m.HomeView), pathMatch: 'full' },
  { path: 'news', loadComponent: () => import('./app/pages/news/news').then(m => m.NewsPage), data: { module: 'articles' } },
  { path: 'news/:id', loadComponent: () => import('./app/pages/content-detail/content-detail').then(m => m.DetailView), data: { module: 'articles' } },
  { path: 'pages', loadComponent: () => import('./app/pages/pages/pages').then(m => m.PagesPage), data: { module: 'pages' } },
  { path: 'pages/:id', loadComponent: () => import('./app/pages/content-detail/content-detail').then(m => m.DetailView), data: { module: 'pages' } },
  { path: 'compositions', loadComponent: () => import('./app/pages/compositions/compositions').then(m => m.CompositionsPage) },
  { path: 'compositions/:id', loadComponent: () => import('./app/pages/compositions/cobweb-detail').then(m => m.CobwebDetailPage) },
  { path: 'contact', loadComponent: () => import('./app/pages/contact/contact').then(m => m.ContactPage) },
  { path: 'site-settings', loadComponent: () => import('./app/pages/site-settings/site-settings').then(m => m.SettingsView) },
  { path: '**', loadComponent: () => import('./app/pages/not-found/not-found').then(m => m.NotFoundView) }
];

async function start() {
  const response = await fetch('/runtime-config.json', { cache: 'no-store' });
  if (!response.ok) throw new Error('Runtime configuration unavailable');
  const config: RuntimeConfig = await response.json();
  if (!Number.isSafeInteger(config.siteId) || config.siteId < 2 || !['mock', 'api'].includes(config.dataMode)
    || config.apiBase !== '/api/v1/public') throw new Error('Invalid runtime configuration');
  await bootstrapApplication(App, {
    providers: [
      provideRouter(routes, withInMemoryScrolling({ scrollPositionRestoration: 'enabled' })),
      { provide: RUNTIME_CONFIG, useValue: config },
      { provide: CONTENT_SOURCE, useValue: config.dataMode === 'api' ? new VibixContentSource(config) : new MockContentSource() }
    ]
  });
}
void start().catch(() => {
  document.body.textContent = 'Le POC ne peut pas démarrer. Vérifiez runtime-config.json : siteId ≥ 2, dataMode mock ou api, apiBase /api/v1/public.';
});
