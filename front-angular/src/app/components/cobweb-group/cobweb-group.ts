import { Component, computed, input, signal } from '@angular/core';
import { CobwebGroup } from '../../content';

@Component({ selector: 'app-cobweb-group', templateUrl: './cobweb-group.html' })
export class CobwebGroupView {
  readonly group = input.required<CobwebGroup>();
  readonly current = signal(0);
  readonly index = computed(() => this.current() % Math.max(this.group().members.length, 1));
  readonly active = computed(() => this.group().members[this.index()]?.item);
  move(delta: number) {
    const length = this.group().members.length;
    if (length) this.current.update(index => (index + delta + length) % length);
  }
}
