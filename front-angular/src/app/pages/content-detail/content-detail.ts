import { Component, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { from, of, switchMap, map, catchError, startWith } from 'rxjs';
import { ContentItem, ContentModule } from '../../content';
import { SiteStore } from '../../site-store';

interface DetailState { loading: boolean; item: ContentItem | null; error: string; }
@Component({ selector: 'app-detail', imports: [RouterLink], templateUrl: './content-detail.html' })
export class DetailView {
  readonly store = inject(SiteStore);
  private readonly route = inject(ActivatedRoute);
  readonly module = this.route.snapshot.data['module'] as ContentModule;
  readonly base = this.module === 'articles' ? '/news' : '/pages';
  readonly state = toSignal(this.route.paramMap.pipe(switchMap(params =>
    from(this.store.readDetail(this.module, params.get('id') ?? '')).pipe(
      map(item => ({ loading: false, item, error: '' } as DetailState)),
      catchError(error => of({ loading: false, item: null, error: error instanceof Error ? error.message : 'Lecture impossible.' } as DetailState)),
      startWith({ loading: true, item: null, error: '' } as DetailState)
    )
  )), { initialValue: { loading: true, item: null, error: '' } as DetailState });
  label(id: string) { return this.store.data()?.navigation[this.module].find(n => n.id === id)?.label ?? 'Rubrique'; }
}
