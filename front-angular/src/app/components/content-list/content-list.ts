import { Component, computed, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { ContentModule } from '../../content';
import { SiteStore } from '../../site-store';
import { descendantIds, navigationRows } from '../../navigation';

@Component({
  selector: 'app-list', imports: [RouterLink],
  templateUrl: './content-list.html'
})
export class ListView {
  readonly store = inject(SiteStore);
  private readonly route = inject(ActivatedRoute);
  readonly module = this.route.snapshot.data['module'] as ContentModule;
  readonly base = this.module === 'articles' ? '/news' : '/pages';
  private readonly params = toSignal(this.route.queryParamMap);
  readonly selected = computed(() => this.params()?.get('category') ?? '');
  readonly nodes = computed(() => navigationRows(this.store.data()?.navigation[this.module] ?? []));
  readonly unknown = computed(() => !!this.selected() && !this.nodes().some(n => n.id === this.selected()));
  readonly selectedLabel = computed(() => this.nodes().find(n => n.id === this.selected())?.label ?? 'Toutes les rubriques');
  readonly items = computed(() => {
    const data = this.store.data();
    if (!data || this.unknown()) return [];
    const ids = descendantIds(data.navigation[this.module], this.selected());
    return data[this.module].filter(item => !this.selected() || ids.has(this.nodes().find(n => n.id === this.selected())?.field === 'group' ? item.groupId ?? '' : item.categoryId))
      .sort((a, b) => b.position - a.position || a.id.localeCompare(b.id));
  });
  label(id: string) { return this.nodes().find(n => n.id === id)?.label ?? 'Rubrique'; }
}
