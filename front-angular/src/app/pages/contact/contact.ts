import { Component, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { SiteStore } from '../../site-store';
import { Challenge, CONTACT_FIELDS, ContactClient, ContactError, ContactPayload, FieldErrors } from './contact-client';

@Component({selector:'app-contact', imports:[FormsModule], templateUrl:'./contact.html'})
export class ContactPage implements OnDestroy {
  readonly store = inject(SiteStore);
  readonly loading = signal(false);
  readonly sending = signal(false);
  readonly error = signal('');
  readonly accepted = signal(false);
  readonly uncertain = signal(false);
  readonly expired = signal(false);
  readonly waitSeconds = signal(0);
  readonly challenge = signal<Challenge | null>(null);
  readonly fieldErrors = signal<FieldErrors>({});
  readonly fieldLabels: Record<string, string> = {name:'Nom', first_name:'Prénom', email:'Courriel', phone:'Téléphone', organization:'Organisation', title:'Objet', message:'Message'};
  readonly fields = CONTACT_FIELDS;
  readonly availableForms = signal<{code:string; label:string}[]>([]);
  readonly previousAnswers = signal<{label:string; value:string}[]>([]);
  selectedType = inject(ActivatedRoute).snapshot.queryParamMap.get('type') || '';
  answers: Record<string, string> = {};
  private lastSchema: Challenge['form'];
  private readonly drafts = new Map<string, {schema:Challenge['form']; answers:Record<string,string>}>();
  private readonly client = new ContactClient(this.store.config);
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private destroyed = false;
  confirmNew = false;
  name = ''; first_name = ''; email = ''; phone = ''; organization = ''; title = ''; message = '';
  constructor() { void this.prepare(); }
  ngOnDestroy() { this.destroyed = true; this.controller?.abort(); clearTimeout(this.timer); }
  private timing() {
    clearTimeout(this.timer);
    if (this.destroyed) return;
    this.waitSeconds.set(Math.max(0, Math.ceil((Math.max(this.client.readyAt, this.client.retryAt) - Date.now()) / 1000)));
    this.expired.set(!!this.challenge() && Date.now() >= this.client.expiresAt);
    if (this.waitSeconds() || (this.challenge() && !this.expired())) this.timer = setTimeout(() => this.timing(), 1000);
  }
  async prepare() {
    if (this.store.config.dataMode !== 'api' || !this.store.data()?.contactsEnabled || this.loading() || this.sending() || this.waitSeconds()) return;
    if (this.uncertain() && !this.confirmNew) return;
    this.client.reset(); this.uncertain.set(false); this.confirmNew = false;
    this.loading.set(true); this.error.set(''); this.fieldErrors.set({}); this.challenge.set(null);
    this.controller = new AbortController();
    try {
      const data = await this.client.prepare(this.controller.signal, this.selectedType);
      if (!this.destroyed) {
        if (data.form) {
          const prior = this.lastSchema;
          if (prior && prior.code === data.form.code && prior.version !== data.form.version) {
            const changed = prior.questions.filter(question => this.answers[question.key] && !data.form!.questions.some(next => next.key === question.key && next.label === question.label));
            this.previousAnswers.set(changed.map(question => ({label:question.label, value:this.answers[question.key]})));
            for (const question of changed) delete this.answers[question.key];
          }
          this.selectedType = data.form.code;
          this.lastSchema = data.form;
          this.availableForms.set(data.forms || []);
        }
        this.challenge.set(data);
      }
    }
    catch (error) { if (!this.destroyed) this.error.set(error instanceof ContactError ? error.message : 'Le formulaire est indisponible.'); }
    finally { if (!this.destroyed) { this.loading.set(false); this.timing(); } }
  }
  async submit() {
    if (this.sending() || this.loading() || !this.challenge() || this.waitSeconds()) return;
    this.sending.set(true); this.error.set(''); this.fieldErrors.set({});
    this.controller = new AbortController();
    const payload = {...Object.fromEntries(this.fields.map(field => [field, this[field]])), ...this.answers} as ContactPayload;
    try {
      await this.client.submit(payload, this.controller.signal);
      if (!this.destroyed) { this.accepted.set(true); this.message = ''; this.uncertain.set(false); this.challenge.set(null); }
    } catch (error) {
      if (!this.destroyed) {
        const failure = error instanceof ContactError ? error : new ContactError('L’envoi n’a pas pu être confirmé.', {}, true);
        this.error.set(failure.message); this.fieldErrors.set(failure.fields); this.uncertain.set(failure.uncertain);
        if (failure.refresh && !failure.uncertain) this.challenge.set(null);
      }
    } finally { if (!this.destroyed) { this.sending.set(false); this.timing(); } }
  }
  async selectForm(code: string) {
    if (this.lastSchema) this.drafts.set(this.lastSchema.code, {schema:this.lastSchema, answers:{...this.answers}});
    const draft = this.drafts.get(code);
    this.selectedType = code; this.answers = {...draft?.answers}; this.lastSchema = draft?.schema;
    this.previousAnswers.set([]); await this.prepare();
  }
  async newMessage() { this.drafts.clear(); this.answers = {}; this.previousAnswers.set([]); this.accepted.set(false); for (const field of this.fields) this[field] = ''; await this.prepare(); }
}
