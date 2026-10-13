import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CobwebGroupView } from '../../components/cobweb-group/cobweb-group';
import { SiteStore } from '../../site-store';

@Component({
  selector: 'app-home', imports: [RouterLink, CobwebGroupView],
  templateUrl: './home.html'
})
export class HomeView {
  readonly store = inject(SiteStore);
  readonly featured = computed(() => this.store.data()?.articles.filter(a => a.inHome).sort((a, b) => b.position - a.position) ?? []);
  readonly slideshows = computed(() => this.store.data()?.cobwebGroups.filter(g => g.presentation === 'slideshow').slice(0, 1) ?? []);
  readonly partners = computed(() => this.store.data()?.cobwebGroups.filter(g => g.partners && g.presentation !== 'slideshow') ?? []);
  label(id: string) { return this.store.data()?.navigation.articles.find(n => n.id === id)?.label ?? 'Actualité'; }
}
