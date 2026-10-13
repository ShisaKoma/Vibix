import { Component, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { catchError, from, map, of, startWith, switchMap } from 'rxjs';
import { CobwebItem } from '../../content';
import { SiteStore } from '../../site-store';

interface State { loading: boolean; item: CobwebItem | null; error: string; }

@Component({
  selector: 'app-cobweb-detail', imports: [RouterLink],
  template: `
    <a class="back-link" routerLink="/compositions">← Toutes les compositions</a>
    @if (state().loading) { <p class="empty" role="status">Chargement de la ressource…</p> }
    @else if (state().item; as item) {
      <article class="article-detail"><p class="eyebrow">COBWEB</p><h1>{{ item.title }}</h1><p class="intro">{{ item.description }}</p>
        @if (item.picture || item.thumbnail; as image) { <img class="detail-image" [src]="image.url" [alt]="image.altText" [width]="image.width" [height]="image.height"> }
        <div class="rich-content" [innerHTML]="item.contentHtml"></div>
        @if (item.link) { <p><a class="button" [href]="item.link" rel="noopener noreferrer">Ouvrir le lien ↗</a></p> }
      </article>
    } @else { <section class="empty"><h1>Ressource indisponible</h1><p role="status">{{ state().error || 'Cette ressource n’existe pas ou n’est plus publiée.' }}</p><a routerLink="/compositions">Revenir aux compositions</a></section> }
  `
})
export class CobwebDetailPage {
  private readonly store = inject(SiteStore);
  private readonly route = inject(ActivatedRoute);
  readonly state = toSignal(this.route.paramMap.pipe(switchMap(params =>
    from(this.store.readCobwebDetail(params.get('id') ?? '')).pipe(
      map(item => ({ loading: false, item, error: '' } as State)),
      catchError(error => of({ loading: false, item: null, error: error instanceof Error ? error.message : 'Lecture impossible.' } as State)),
      startWith({ loading: true, item: null, error: '' } as State)
    )
  )), { initialValue: { loading: true, item: null, error: '' } as State });
}
