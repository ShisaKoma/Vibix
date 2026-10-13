"use strict";

document.querySelectorAll('[data-addressbook-form]').forEach(form => {
  const kindSelect = form.querySelector('#id_kind');
  if (!kindSelect) return;

  const update = () => {
    const kind = kindSelect.value;
    form.querySelectorAll('[data-addressbook-field]').forEach(group => {
      const target = group.dataset.addressbookField;
      const relevant = target === 'person' ? kind === 'person' : kind === 'company' || kind === 'association';
      group.hidden = !relevant && !group.querySelector('.errorlist');
      group.querySelectorAll('input, select, textarea').forEach(field => {
        field.disabled = !relevant;
        if (!relevant) field.value = '';
      });
    });
    form.querySelectorAll('select').forEach(select => {
      const options = [...select.options].filter(option => option.hasAttribute('data-addressbook-kind'));
      if (!options.length) return;
      options.forEach(option => {
        const scope = option.dataset.addressbookKind;
        const available = Boolean(kind) && (!scope || scope === kind);
        option.hidden = !available;
        option.disabled = !available;
        if (!available && option.selected) select.value = '';
      });
    });
  };

  kindSelect.addEventListener('change', update);
  update();
});
