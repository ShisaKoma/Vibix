export const CONTACT_FIELDS = ['name', 'first_name', 'email', 'phone', 'organization', 'title', 'message'] as const;
export const QUESTION_FIELDS = Array.from({length:10}, (_, index) => `question_${String(index + 1).padStart(2, '0')}`);
export type ContactPayload = Record<typeof CONTACT_FIELDS[number], string> & Record<string, string>;
export interface FormDefinition {code: string; label: string; version: string; questions: {key:string; label:string; required:boolean}[];}
export type FieldErrors = Record<string, {message: string}[]>;
export interface Challenge {
  challenge: string; csrf_token: string; expires_in: number; minimum_delay: number;
  max_message_length: number; retention_days: number;
  form?: FormDefinition; forms?: {code:string; label:string}[];
}
export class ContactError extends Error {
  constructor(message: string, readonly fields: FieldErrors = {}, readonly uncertain = false, readonly refresh = false) { super(message); }
}
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
function validForm(value: unknown): value is FormDefinition {
  if (!record(value) || typeof value['code'] !== 'string' || !/^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(value['code']) || value['code'].length > 64 || typeof value['label'] !== 'string' || !value['label'].length || value['label'].length > 200 || typeof value['version'] !== 'string' || !value['version'].length || value['version'].length > 64 || !Array.isArray(value['questions']) || value['questions'].length > 10) return false;
  const questions = value['questions'];
  return questions.every(question => record(question) && typeof question['key'] === 'string' && QUESTION_FIELDS.includes(question['key']) && typeof question['label'] === 'string' && question['label'].length > 0 && question['label'].length <= 200 && typeof question['required'] === 'boolean') && new Set(questions.map(question => question['key'])).size === questions.length;
}
export function validChallenge(value: unknown): value is Challenge {
  if (!record(value)) return false;
  const definition = value['form'], forms = value['forms'];
  return typeof value['challenge'] === 'string' && value['challenge'].length > 0 && value['challenge'].length <= 2000
    && typeof value['csrf_token'] === 'string' && /^[a-zA-Z0-9]{32,64}$/.test(value['csrf_token'])
    && value['expires_in'] === 1800 && value['minimum_delay'] === 2
    && Number.isInteger(value['max_message_length']) && Number(value['max_message_length']) >= 100 && Number(value['max_message_length']) <= 50000
    && Number.isInteger(value['retention_days']) && Number(value['retention_days']) >= 30 && Number(value['retention_days']) <= 3650
    && ((definition === undefined && forms === undefined) || (validForm(definition) && Array.isArray(forms) && forms.length > 0 && forms.length <= 100 && forms.every(item => record(item) && typeof item['code'] === 'string' && /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(item['code']) && item['code'].length <= 64 && typeof item['label'] === 'string' && item['label'].length > 0 && item['label'].length <= 200) && forms.some(item => item['code'] === definition.code)));
}
function validationFields(value: unknown): FieldErrors {
  if (!record(value)) return {};
  const fields: FieldErrors = {};
  for (const field of [...CONTACT_FIELDS, ...QUESTION_FIELDS, "form_type", "form_version"]) {
    const issues = value[field];
    if (Array.isArray(issues) && issues.length <= 20 && issues.every(issue => record(issue) && typeof issue['message'] === 'string' && issue['message'].length <= 1000)) fields[field] = issues.map(issue => ({message: issue['message']}));
  }
  return fields;
}

/** Same-origin browser connector; never stores a Bearer token or personal data on disk. */
export class ContactClient {
  private token: Challenge | null = null;
  private body: string | null = null;
  private unknownResult = false;
  readyAt = 0;
  expiresAt = 0;
  retryAt = 0;
  private readonly base: string;
  constructor(config: {siteId: number; apiBase: string}, private readonly fetcher: typeof fetch = globalThis.fetch.bind(globalThis), private readonly now: () => number = Date.now) {
    if (!Number.isSafeInteger(config.siteId) || config.siteId < 2 || config.apiBase !== '/api/v1/public') throw new Error('Configuration Contacts invalide.');
    this.base = `${config.apiBase}/sites/${config.siteId}/contacts/`;
  }
  reset() { this.token = null; this.body = null; this.unknownResult = false; this.readyAt = this.expiresAt = 0; }
  private quota(response: Response) {
    const raw = response.headers.get('Retry-After');
    const delay = raw && /^\d+$/.test(raw) ? Math.max(1, Math.min(3600, Number(raw))) : 5;
    this.retryAt = this.now() + delay * 1000;
    return new ContactError('Trop de demandes. Respectez le délai avant de réessayer.', {}, this.unknownResult);
  }
  async prepare(signal?: AbortSignal, formType = ""): Promise<Challenge> {
    if (this.body) throw new ContactError('Réessayez d’abord le même envoi pour en vérifier la réception.', {}, true);
    if (this.now() < this.retryAt) throw new ContactError('Attendez la fin du délai avant de recharger.');
    this.reset();
    const started = this.now();
    try {
      const response = await this.fetcher(`${this.base}challenge/${formType ? `?type=${encodeURIComponent(formType)}` : ""}`, {credentials:'same-origin', cache:'no-store', signal:signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)});
      if (response.status === 429) throw this.quota(response);
      if (!response.ok) throw new ContactError(response.status === 404 ? 'Ce site n’accepte pas de demandes de contact actuellement.' : 'Le formulaire de contact est indisponible.');
      const data: unknown = await response.json();
      if (!validChallenge(data)) throw new ContactError('Le formulaire retourné n’est pas valide.');
      this.token = data; this.readyAt = this.now() + data.minimum_delay * 1000; this.expiresAt = started + data.expires_in * 1000;
      return data;
    } catch (error) { throw error instanceof ContactError ? error : new ContactError('Le formulaire n’a pas pu être chargé. Réessayez.'); }
  }
  async submit(payload: ContactPayload, signal?: AbortSignal): Promise<void> {
    if (!this.token || (!this.body && this.now() >= this.expiresAt)) throw new ContactError('Le formulaire a expiré. Rechargez-le : votre texte sera conservé.', {}, false, true);
    if (this.now() < Math.max(this.readyAt, this.retryAt)) throw new ContactError('Attendez la fin du délai avant d’envoyer.', {}, this.unknownResult);
    if (!this.body) {
      const schema = this.token.form;
      this.body = JSON.stringify({...Object.fromEntries(CONTACT_FIELDS.map(field => [field, payload[field]])), ...(schema ? {form_type:schema.code, form_version:schema.version, ...Object.fromEntries(schema.questions.map(question => [question.key, payload[question.key] || '']))} : {}), challenge:this.token.challenge, website:''});
    }
    try {
      const response = await this.fetcher(this.base, {method:'POST', credentials:'same-origin', cache:'no-store', signal:signal ? AbortSignal.any([signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000), headers:{'Content-Type':'application/json', 'X-CSRFToken':this.token.csrf_token}, body:this.body});
      if (response.status === 429) throw this.quota(response);
      const data: unknown = await response.json().catch(() => null);
      if (response.status === 202 && record(data) && data['status'] === 'accepted') { this.reset(); return; }
      if (response.status === 202 || response.status >= 500) throw new ContactError('La réception n’a pas pu être confirmée. Réessayez le même envoi.', {}, true);
      if (response.status === 403 || (record(data) && data['error'] === 'invalid_challenge')) throw new ContactError('La protection du formulaire a expiré. Rechargez le formulaire.', {}, this.unknownResult, true);
      if (record(data) && data['error'] === 'form_changed') throw new ContactError('Le formulaire a changé. Rechargez-le pour vérifier les questions.', {}, this.unknownResult, true);
      if (record(data) && data['error'] === 'validation_failed' && record(data['fields']) && (data['fields']['form_version'] || data['fields']['form_type'])) throw new ContactError('Le formulaire a changé. Rechargez-le pour vérifier les questions.', validationFields(data['fields']), this.unknownResult, true);
      if (response.status === 409) throw new ContactError('Ce formulaire a déjà servi à une autre demande. Vérifiez l’envoi précédent avant de recommencer.', {}, this.unknownResult, true);
      if (response.status === 404) throw new ContactError('Ce site n’accepte plus de demandes de contact.', {}, this.unknownResult, true);
      if (response.status === 413) throw new ContactError('Le message est trop volumineux.', {}, this.unknownResult);
      throw new ContactError('Vérifiez les champs de votre demande.', record(data) && data['error'] === 'validation_failed' ? validationFields(data['fields']) : {}, this.unknownResult);
    } catch (error) {
      const failure = error instanceof ContactError ? error : new ContactError('La réception n’a pas pu être confirmée. Réessayez le même envoi.', {}, true);
      this.unknownResult = failure.uncertain;
      if (!failure.uncertain) this.body = null;
      throw failure;
    }
  }
}
