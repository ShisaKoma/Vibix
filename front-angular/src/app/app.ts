import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { SiteStore } from './site-store';

@Component({
  selector: 'app-root', imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <a class="skip-link" href="#main">Aller au contenu</a>
    <div class="poc-banner">POC · {{ store.config.dataMode === 'api' ? 'API publique ViBix' : 'données de démonstration' }} <span>Site {{ store.config.siteId }} · {{ store.config.dataMode === 'api' ? 'mode API' : 'mode local' }}</span></div>
    <header class="site-header">
      <a class="brand" routerLink="/">
        @if (store.data()?.settings?.logoUrl; as logo) { <img [src]="logo" alt=""> } @else { <span class="brand-mark" aria-hidden="true">v.</span> }
        <span>{{ store.data()?.settings?.title || 'ViBix' }}</span>
      </a>
      <nav aria-label="Navigation principale">
        <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{exact:true}" ariaCurrentWhenActive="page">Accueil</a>
        @if (store.data()?.articlesEnabled !== false) { <a routerLink="/news" routerLinkActive="active" ariaCurrentWhenActive="page">Actualités</a> }
        @if (store.data()?.planningEnabled) { <a routerLink="/events" routerLinkActive="active" ariaCurrentWhenActive="page">Événements</a> }
        @if (store.data()?.productsEnabled) { <a routerLink="/products" routerLinkActive="active" ariaCurrentWhenActive="page">Produits</a> }
        @if (store.data()?.pagesEnabled !== false) { <a routerLink="/pages" routerLinkActive="active" ariaCurrentWhenActive="page">Pages</a> }
        @if (store.data()?.cobwebEnabled !== false) { <a routerLink="/compositions" routerLinkActive="active" ariaCurrentWhenActive="page">Compositions</a> }
        @if (store.data()?.contactsEnabled) { <a routerLink="/contact" routerLinkActive="active" ariaCurrentWhenActive="page">Contact</a> }
        <a class="settings-link" routerLink="/site-settings" routerLinkActive="active" ariaCurrentWhenActive="page">Site-settings <span aria-hidden="true">↗</span></a>
      </nav>
    </header>
    <main id="main" tabindex="-1">
      @if (store.loading()) { <p class="empty" role="status">Chargement du site…</p> }
      @else if (store.error()) { <section class="empty"><h1>Site indisponible</h1><p role="alert">{{ store.error() }}</p><button class="button" (click)="store.load()">Réessayer</button></section> }
      @else { <router-outlet /> }
    </main>
    <footer><span>{{ store.data()?.settings?.title || 'ViBix' }}</span><p>Un petit espace pour les grandes idées.</p><span class="footer-note">Prototype interne · Angular CSR</span></footer>
  `
})
export class App {
  readonly store = inject(SiteStore);
  constructor() { void this.store.load(); }
}
