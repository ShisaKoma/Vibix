import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';

function initialContent(value) {
  if (/<[a-z][\s\S]*>/i.test(value)) return value;
  return {
    type: 'doc', content: value.split('\n').map(text => ({
      type: 'paragraph', content: text ? [{type: 'text', text}] : [],
    })),
  };
}

document.querySelectorAll('textarea.rich-text-source').forEach(field => {
  const wrapper = document.createElement('div');
  wrapper.className = 'visual-editor';
  const toolbar = document.createElement('div');
  toolbar.className = 'editor-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', 'Mise en forme du contenu');
  toolbar.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || event.target.tagName === 'SELECT') return;
    const controls = [...toolbar.querySelectorAll('button:not(:disabled),select')];
    const current = controls.indexOf(event.target);
    if (current < 0) return;
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
      : (current + (event.key === 'ArrowLeft' ? -1 : 1) + controls.length) % controls.length;
    event.preventDefault(); controls[index].focus();
  });
  const canvas = document.createElement('div');
  const status = document.createElement('p');
  status.className = 'editor-status';
  wrapper.append(toolbar, canvas, status);
  field.before(wrapper);
  let editor;
  const buttons = [];
  const button = (label, text, command, active) => {
    const control = document.createElement('button');
    control.type = 'button';
    control.className = 'editor-tool';
    control.textContent = text;
    control.title = label;
    control.setAttribute('aria-label', label);
    if (active) control.setAttribute('aria-pressed', 'false');
    control.addEventListener('click', command);
    toolbar.append(control);
    buttons.push({control, active});
    return control;
  };
  const formatLabel = document.createElement('label');
  formatLabel.className = 'visually-hidden';
  formatLabel.textContent = 'Format du paragraphe';
  const format = document.createElement('select');
  format.id = `${field.id}_format`;
  formatLabel.htmlFor = format.id;
  [['p', 'Paragraphe'], ['2', 'Titre 2'], ['3', 'Titre 3']].forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    format.append(option);
  });
  toolbar.append(formatLabel, format);
  format.addEventListener('change', () => {
    const chain = editor.chain().focus();
    if (format.value === 'p') chain.setParagraph().run();
    else chain.setHeading({level: Number(format.value)}).run();
  });
  button('Gras', 'B', () => editor.chain().focus().toggleBold().run(), 'bold');
  button('Italique', 'I', () => editor.chain().focus().toggleItalic().run(), 'italic');
  button('Souligner', 'U', () => editor.chain().focus().toggleUnderline().run(), 'underline');
  button('Barrer', 'S', () => editor.chain().focus().toggleStrike().run(), 'strike');
  button('Liste à puces', '• Liste', () => editor.chain().focus().toggleBulletList().run(), 'bulletList');
  button('Liste numérotée', '1. Liste', () => editor.chain().focus().toggleOrderedList().run(), 'orderedList');
  button('Citation', '❝', () => editor.chain().focus().toggleBlockquote().run(), 'blockquote');

  const dialog = document.createElement('dialog');
  dialog.className = 'editor-link-dialog';
  dialog.setAttribute('aria-labelledby', `${field.id}_link_title`);
  const title = document.createElement('h2');
  title.id = `${field.id}_link_title`;
  title.textContent = 'Insérer ou modifier un lien';
  const label = document.createElement('label');
  label.textContent = 'Adresse du lien';
  const url = document.createElement('input');
  url.type = 'url';
  url.id = `${field.id}_link_url`;
  url.placeholder = 'https://exemple.fr';
  label.htmlFor = url.id;
  const error = document.createElement('p');
  error.className = 'errorlist';
  error.setAttribute('role', 'alert');
  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'Annuler';
  cancel.className = 'secondary-button';
  const apply = document.createElement('button');
  apply.type = 'button';
  apply.textContent = 'Appliquer le lien';
  actions.append(cancel, apply);
  dialog.append(title, label, url, error, actions);
  wrapper.append(dialog);
  cancel.addEventListener('click', () => dialog.close());
  const setLink = () => {
    try {
      const address = new URL(url.value.trim());
      if (!['http:', 'https:'].includes(address.protocol)) throw new Error('scheme');
      editor.chain().focus().extendMarkRange('link').setLink({href: address.href}).run();
      dialog.close();
    } catch {
      error.textContent = 'Saisissez une adresse complète commençant par https:// ou http://.';
      url.focus();
    }
  };
  apply.addEventListener('click', setLink);
  url.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); setLink(); }
  });
  button('Insérer un lien', 'Lien', () => {
    url.value = editor.getAttributes('link').href || '';
    error.textContent = '';
    dialog.showModal();
    url.focus();
  }, 'link');
  button('Retirer le lien', 'Délier', () => editor.chain().focus().extendMarkRange('link').unsetLink().run());
  const undo = button('Annuler la dernière modification', '↶', () => editor.chain().focus().undo().run());
  const redo = button('Rétablir la modification', '↷', () => editor.chain().focus().redo().run());
  button('Retirer la mise en forme', 'Tx', () => editor.chain().focus().unsetAllMarks().clearNodes().run());

  const update = () => {
    buttons.forEach(({control, active}) => {
      if (active) control.setAttribute('aria-pressed', String(editor.isActive(active)));
    });
    format.value = editor.isActive('heading', {level: 2}) ? '2' : editor.isActive('heading', {level: 3}) ? '3' : 'p';
    undo.disabled = !editor.can().undo();
    redo.disabled = !editor.can().redo();
    const text = editor.getText().trim();
    const words = text ? text.split(/\s+/).length : 0;
    status.textContent = `${words} mot${words > 1 ? 's' : ''} · ${text.length} caractères`;
  };
  try {
    const fieldLabel = field.closest('.form-field')?.querySelector('label');
    editor = new Editor({
      element: canvas,
      injectCSS: false,
      extensions: [StarterKit.configure({
        heading: {levels: [2, 3]}, code: false, codeBlock: false, horizontalRule: false,
        link: {openOnClick: false, autolink: false, linkOnPaste: false,
          protocols: ['http', 'https'], isAllowedUri: url => /^https?:\/\//i.test(url),
          HTMLAttributes: {rel: 'noopener noreferrer', target: null}},
      })],
      content: initialContent(field.value),
      editorProps: {attributes: {
        id: `${field.id}_editor`, role: 'textbox', 'aria-multiline': 'true',
        'aria-label': fieldLabel?.textContent.replace(/\s+/g, ' ').trim() || 'Contenu',
        'aria-describedby': field.getAttribute('aria-describedby') || '',
        'aria-invalid': field.getAttribute('aria-invalid') || 'false',
      }},
      onUpdate: ({editor}) => {
        field.value = editor.isEmpty ? '' : editor.getHTML();
        field.dispatchEvent(new Event('input', {bubbles: true}));
        update();
      },
      onSelectionUpdate: update,
      onTransaction: () => { if (editor) update(); },
    });
    field.hidden = true;
    field.tabIndex = -1;
    fieldLabel?.addEventListener('click', () => editor.commands.focus());
    field.form.addEventListener('submit', () => {
      field.value = editor.isEmpty ? '' : editor.getHTML();
    });
    field.addEventListener('focus', () => editor.commands.focus());
    update();
  } catch (failure) {
    wrapper.remove();
    field.hidden = false;
    console.error('L’éditeur visuel ne peut pas être initialisé ; le champ texte reste disponible.', failure);
  }
});
