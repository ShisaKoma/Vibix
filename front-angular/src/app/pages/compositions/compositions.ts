import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SiteStore } from '../../site-store';
import { CobwebGroupView } from '../../components/cobweb-group/cobweb-group';

@Component({ selector: 'app-compositions-page', imports: [CobwebGroupView, RouterLink], templateUrl: './compositions.html' })
export class CompositionsPage {
  readonly store = inject(SiteStore);
  readonly groups = computed(() => this.store.data()?.cobwebGroups ?? []);
  readonly items = computed(() => this.store.data()?.cobwebItems ?? []);
}
