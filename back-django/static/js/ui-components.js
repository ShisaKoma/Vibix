"use strict";
const normalized = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr');

document.querySelectorAll('[data-slideshow]').forEach(slideshow => {
  const slides = [...slideshow.querySelectorAll('[data-slide]')];
  if (slides.length < 2) return;
  let current = 0;
  const status = slideshow.querySelector('[data-slide-status]');
  const show = index => {
    current = (index + slides.length) % slides.length;
    slides.forEach((slide, i) => { slide.hidden = i !== current; });
    if (status) status.textContent = `${current + 1} / ${slides.length}`;
  };
  slideshow.querySelector('[data-slide-previous]')?.addEventListener('click', () => show(current - 1));
  slideshow.querySelector('[data-slide-next]')?.addEventListener('click', () => show(current + 1));
});

document.querySelectorAll('[data-tree-control]').forEach(control => {
  const root = control.querySelector('[data-tree-root]');
  const nodes = [...root.querySelectorAll('[data-tree-node]')];
  if (!nodes.length) return;
  const multiple = control.dataset.selection === 'multiple';
  const input = node => node.querySelector('.tree-row input');
  const label = node => node.querySelector('[data-tree-label]').textContent;
  const children = node => node.querySelector(':scope > .tree-children');
  const parent = node => node.parentElement.closest('[data-tree-node]');
  const status = control.querySelector('[data-tree-status]');
  const filter = control.querySelector('[data-tree-filter]');
  const expanded = new Map();
  const visible = () => nodes.filter(node => !node.closest('[hidden]'));
  const focus = node => {
    if (!node) return;
    nodes.forEach(entry => entry.tabIndex = entry === node ? 0 : -1);
    node.focus();
  };
  const expand = (node, open, remember = true) => {
    if (!children(node)) return;
    node.setAttribute('aria-expanded', String(open));
    children(node).hidden = !open;
    node.querySelector('[data-tree-toggle]').setAttribute('aria-label', `${open ? 'Replier' : 'Déplier'} ${label(node)}`);
    if (remember) expanded.set(node, open);
  };
  const selection = () => {
    const selected = nodes.filter(node => input(node).checked);
    nodes.forEach(node => node.setAttribute(multiple ? 'aria-checked' : 'aria-selected', String(input(node).checked)));
    if (multiple) status.textContent = `${selected.length} catégorie${selected.length > 1 ? 's' : ''} sélectionnée${selected.length > 1 ? 's' : ''}`;
    else if (selected.length) {
      const path = []; let node = selected[0];
      while (node) { path.unshift(label(node)); node = parent(node); }
      status.textContent = `Sélection : ${path.join(' › ')}`;
      control.querySelector('.tree-validation').hidden = true;
    } else status.textContent = control.hasAttribute('data-tree-optional') ? 'Aucune sélection.' : 'Sélectionnez une catégorie.';
  };
  const choose = node => {
    const field = input(node);
    field.checked = multiple ? !field.checked : true;
    field.dispatchEvent(new Event('change', {bubbles: true}));
    selection();
  };
  root.setAttribute('role', 'tree');
  if (multiple) root.setAttribute('aria-multiselectable', 'true');
  else root.setAttribute('aria-required', String(!control.hasAttribute('data-tree-optional')));
  control.classList.add('tree-enhanced');
  control.querySelector('.tree-tools').hidden = false;
  nodes.forEach(node => {
    node.setAttribute('role', 'treeitem');
    node.setAttribute('aria-labelledby', node.querySelector('[data-tree-label]').id);
    node.tabIndex = -1;
    const field = input(node);
    field.tabIndex = -1;
    field.setAttribute('aria-hidden', 'true');
    if (children(node)) {
      children(node).setAttribute('role', 'group');
      node.querySelector('[data-tree-toggle]').hidden = false;
      expand(node, Boolean(node.querySelector('input:checked')));
    }
    node.querySelector('.tree-node-label').addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation(); choose(node); focus(node);
    });
    node.querySelector('[data-tree-toggle]')?.addEventListener('click', event => {
      event.stopPropagation(); expand(node, node.getAttribute('aria-expanded') !== 'true'); focus(node);
    });
    field.addEventListener('change', selection);
    node.addEventListener('focus', () => {
      nodes.forEach(entry => entry.tabIndex = entry === node ? 0 : -1);
    });
  });
  control.closest('form')?.addEventListener('change', event => {
    if (event.target.name === input(nodes[0]).name) selection();
  });
  const initial = nodes.find(node => input(node).checked) || nodes[0];
  initial.tabIndex = 0;
  let typed = '', typedAt = 0;
  root.addEventListener('keydown', event => {
    const node = event.target.closest('[data-tree-node]');
    if (!node || event.target !== node) return;
    const shown = visible(), index = shown.indexOf(node);
    const handlers = {
      ArrowDown: () => focus(shown[Math.min(index + 1, shown.length - 1)]),
      ArrowUp: () => focus(shown[Math.max(index - 1, 0)]),
      Home: () => focus(shown[0]), End: () => focus(shown.at(-1)),
      ArrowRight: () => {
        if (!children(node)) return;
        if (node.getAttribute('aria-expanded') !== 'true') expand(node, true);
        else focus(children(node).querySelector('[data-tree-node]'));
      },
      ArrowLeft: () => {
        if (node.getAttribute('aria-expanded') === 'true') expand(node, false);
        else focus(parent(node));
      },
      Enter: () => choose(node), ' ': () => choose(node),
    };
    if (handlers[event.key]) { event.preventDefault(); handlers[event.key](); }
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      typed = now - typedAt < 650 ? typed + event.key : event.key;
      typedAt = now;
      const ordered = [...shown.slice(index + 1), ...shown.slice(0, index + 1)];
      focus(ordered.find(candidate => normalized(label(candidate)).startsWith(normalized(typed))));
    }
  });
  filter.addEventListener('input', () => {
    const term = normalized(filter.value.trim());
    let matches = 0;
    [...nodes].reverse().forEach(node => {
      const ownMatch = normalized(label(node)).includes(term);
      const childMatch = children(node) && [...children(node).children].some(child => !child.hidden);
      node.hidden = !(ownMatch || childMatch);
      if (ownMatch) matches += 1;
      node.classList.toggle('tree-match', Boolean(term && ownMatch));
      expand(node, term ? Boolean(childMatch) : expanded.get(node), false);
    });
    control.querySelector('.tree-empty').hidden = matches > 0;
    const first = visible()[0];
    nodes.forEach(node => node.tabIndex = node === first ? 0 : -1);
  });
  control.querySelector('[data-tree-expand]').addEventListener('click', () => nodes.forEach(node => expand(node, true)));
  control.querySelector('[data-tree-collapse]').addEventListener('click', () => {
    nodes.forEach(node => expand(node, false));
    nodes.forEach(node => node.tabIndex = node === nodes[0] ? 0 : -1);
  });
  root.addEventListener('focusin', event => {
    const node = event.target.closest('[data-tree-node]');
    if (node && event.target === input(node)) focus(node);
  });
  selection();
});

document.querySelectorAll('[data-member-picker]').forEach(picker => {
  const list = picker.querySelector('.member-list');
  const rows = [...list.querySelectorAll('[data-member-row]')];
  const checked = row => row.querySelector('input[type=checkbox]').checked;
  const position = row => row.querySelector('[data-member-position]');
  const selected = () => [...list.children].filter(row => row.matches('[data-member-row]') && checked(row));
  picker.querySelector('.member-tools').hidden = false;
  const update = () => {
    const chosen = selected();
    picker.querySelector('[data-member-count]').textContent = `${chosen.length} entrée${chosen.length > 1 ? 's' : ''} sélectionnée${chosen.length > 1 ? 's' : ''}`;
    rows.forEach(row => {
      row.classList.toggle('is-selected', checked(row));
      const index = chosen.indexOf(row);
      row.querySelector('[data-member-up]').disabled = index <= 0;
      row.querySelector('[data-member-down]').disabled = index < 0 || index === chosen.length - 1;
    });
  };
  const sort = () => {
    rows.sort((a, b) => Number(checked(b)) - Number(checked(a)) || (checked(a) ? Number(position(a).value) - Number(position(b).value) : 0)).forEach(row => list.append(row));
    update();
  };
  const move = (row, offset) => {
    const chosen = selected(), index = chosen.indexOf(row), other = chosen[index + offset];
    if (!other) return;
    chosen[index] = other; chosen[index + offset] = row;
    chosen.forEach((entry, rank) => {
      position(entry).value = String(rank + 1);
      list.append(entry);
    });
    rows.filter(entry => !checked(entry)).forEach(entry => list.append(entry));
    position(row).dispatchEvent(new Event('input', {bubbles: true}));
    update();
  };
  rows.forEach(row => {
    const up = row.querySelector('[data-member-up]'), down = row.querySelector('[data-member-down]');
    up.hidden = down.hidden = false;
    up.addEventListener('click', () => move(row, -1));
    down.addEventListener('click', () => move(row, 1));
    row.querySelector('input[type=checkbox]').addEventListener('change', () => {
      if (checked(row)) position(row).value = String(Math.max(0, ...selected().map(entry => Number(position(entry).value))) + 1);
      sort();
    });
    position(row).addEventListener('change', sort);
  });
  picker.querySelector('[data-member-filter]').addEventListener('input', event => {
    const term = normalized(event.target.value.trim());
    rows.forEach(row => row.hidden = !normalized(row.querySelector('.member-choice').textContent).includes(term));
    picker.querySelector('.member-empty').hidden = rows.some(row => !row.hidden);
  });
  sort();
});

document.querySelectorAll('[data-record-flow]').forEach(form => {
  const wizard = form.dataset.recordFlow === 'wizard';
  const sections = new Map([...form.querySelectorAll('[data-record-section]')].map(section => [section.dataset.recordSection, section]));
  const definitions = wizard ? [
    ['fiche', 'Fiche', ['informations', 'fiche']],
    ['contenu', 'Contenu et médias', ['contenu', 'medias']],
    ['liaisons', 'Liaisons', ['liaisons']],
    ['publication', 'Publication', ['publication', 'referencement']],
    ['verification', 'Vérification', ['verification']],
  ] : [
    ['informations', 'Informations', ['informations']],
    ['fiche', 'Fiche', ['fiche', 'contenu']],
    ['medias', 'Médias', ['medias']],
    ['liaisons', 'Liaisons', ['liaisons']],
    ['publication', 'Visibilité', ['publication']],
    ['referencement', 'Référencement', ['referencement']],
  ];
  const container = form.querySelector('[data-flow-content]');
  const navigation = form.querySelector('[data-flow-nav]');
  const progress = form.querySelector('[data-flow-progress]');
  const previous = form.querySelector('[data-flow-previous]');
  const next = form.querySelector('[data-flow-next]');
  const saves = [...form.querySelectorAll('[data-flow-save]')];
  const state = form.querySelector('[data-flow-state]');
  const nav = document.createElement(wizard ? 'ol' : 'div');
  nav.className = wizard ? 'flow-stepper' : 'flow-tabs';
  if (!wizard) { nav.setAttribute('role', 'tablist'); nav.setAttribute('aria-label', 'Sections de la fiche'); }
  else nav.setAttribute('aria-label', 'Étapes de création');
  const groups = definitions.map(([id, title, keys], index) => {
    const panel = document.createElement('div');
    panel.className = 'flow-panel'; panel.id = `record-panel-${id}`;
    const button = document.createElement('button');
    button.type = 'button'; button.id = `record-nav-${id}`;
    button.className = wizard ? 'flow-step' : 'flow-tab';
    button.setAttribute('aria-controls', panel.id);
    panel.setAttribute('aria-labelledby', button.id);
    panel.setAttribute('role', wizard ? 'region' : 'tabpanel');
    if (!wizard) button.setAttribute('role', 'tab');
    const number = document.createElement('span');
    number.className = 'step-number'; number.textContent = String(index + 1);
    number.setAttribute('aria-hidden', 'true');
    const caption = document.createElement('span'); caption.textContent = title;
    if (wizard) button.append(number);
    button.append(caption);
    keys.forEach(key => {
      const section = sections.get(key);
      if (section) { section.hidden = false; panel.append(section); }
    });
    const errors = panel.querySelectorAll('.errorlist li').length;
    if (errors) {
      const badge = document.createElement('span'); badge.className = 'tab-error';
      badge.textContent = String(errors); button.append(badge);
      button.setAttribute('aria-label', `${title}, ${errors} erreur${errors > 1 ? 's' : ''}`);
    }
    if (wizard) { const item = document.createElement('li'); item.append(button); nav.append(item); }
    else nav.append(button);
    container.append(panel);
    return {id, title, panel, button, errors};
  });
  navigation.append(nav); navigation.hidden = progress.hidden = false;
  const requested = groups.findIndex(group => group.id === state.value);
  const errorIndex = groups.findIndex(group => group.errors > 0);
  let current = errorIndex >= 0 ? errorIndex : requested >= 0 ? requested : wizard ? 0 : 1;
  let unlocked = wizard && form.querySelector('.form-errors') ? current : 0;
  const titleField = form.querySelector('[name=title]');
  const summary = () => {
    const review = form.querySelector('[data-flow-review]');
    if (!review) return;
    review.replaceChildren();
    const value = name => form.querySelector(`[name=${name}]`);
    const selectedLabel = name => value(name)?.value ? value(name).selectedOptions[0].textContent : 'Aucun';
    const rows = [
      ['Titre', titleField.value || 'Non renseigné'],
      ['Catégorie', form.querySelector('[data-selection=single] [data-tree-status]')?.textContent.replace('Sélection : ', '') || 'Non renseignée'],
      ['Contenu', form.querySelector('.tiptap')?.textContent || value('content')?.value || 'Aucun contenu'],
      ['Visuel intérieur', selectedLabel('picture')],
      ['Visuel de liste', selectedLabel('thumbnail')],
      ['Visibilité', value('is_online').checked ? 'Publication autorisée selon les règles du site' : 'Hors ligne'],
      ['Début de publication', value('starts_at').value.replace('T', ' à ') || 'Immédiatement'],
      ['Fin de publication', value('ends_at').value.replace('T', ' à ') || 'Sans date de fin'],
    ];
    if (value('language')) {
      rows.push(['Langue', selectedLabel('language')]);
      if (value('in_home')) rows.push(['Accueil', value('in_home').checked ? 'Affichage autorisé si l’article est publiable' : 'Non']);
      const images = [...form.querySelectorAll('[name^=gallery_]')].filter(field => field.value).length + Number(Boolean(value('picture').value));
      rows.push(['Galerie', `${images} image${images > 1 ? 's' : ''}`]);
    }
    const members = [...form.querySelectorAll('[data-member-row]')].filter(row => row.querySelector('[name=members]').checked);
    if (value('slug')) rows.push(['Adresse', value('slug').value || 'Non renseignée']);
    if (value('group')) rows.push(['Groupe', form.querySelector('[name=group]:checked')?.closest('label')?.textContent.trim() || 'Aucun groupe']);
    if (form.querySelector('[data-member-picker]')) rows.push(['Composition', members.map(row => row.querySelector('[data-member-title]').textContent).join(' → ') || 'Aucune entrée']);
    rows.push(['Contenus associés', String(form.querySelectorAll('[name=targets]:checked').length)]);
    rows.forEach(([label, text]) => {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = text;
      review.append(dt, dd);
    });
  };
  const show = (index, focusHeading = false) => {
    current = index; state.value = groups[index].id;
    groups.forEach((group, rank) => {
      const active = rank === index;
      group.panel.hidden = !active;
      group.button.classList.toggle('is-active', active);
      if (wizard) {
        group.button.disabled = rank > unlocked;
        group.button.classList.toggle('is-complete', rank < index);
        if (active) group.button.setAttribute('aria-current', 'step');
        else group.button.removeAttribute('aria-current');
      } else {
        group.button.setAttribute('aria-selected', String(active));
        group.button.tabIndex = active ? 0 : -1;
      }
    });
    previous.hidden = !wizard || index === 0;
    next.hidden = !wizard || index === groups.length - 1;
    saves.forEach(save => save.hidden = wizard && index !== groups.length - 1);
    progress.textContent = wizard ? `Étape ${index + 1} sur ${groups.length} — ${groups[index].title}` : groups[index].title;
    if (wizard && index === groups.length - 1) summary();
    if (focusHeading) {
      const heading = groups[index].panel.querySelector('h2');
      heading.tabIndex = -1; heading.focus();
    }
  };
  const validate = index => {
    const panel = groups[index].panel;
    const category = panel.querySelector('[data-selection=single]:not([data-tree-optional])');
    if (category && !category.querySelector('input:checked')) {
      category.querySelector('.tree-validation').hidden = false;
      show(index);
      category.querySelector('[role=treeitem][tabindex="0"]')?.focus();
      return false;
    }
    const start = panel.querySelector('[name=starts_at]'), end = panel.querySelector('[name=ends_at]');
    if (end && !end.disabled) end.setCustomValidity(start.value && end.value && start.value >= end.value ? 'La fin doit être postérieure au début.' : '');
    for (const field of panel.querySelectorAll('input,select,textarea')) {
      if (field.disabled || field.type === 'hidden' || field.closest('dialog')) continue;
      if (field.matches('[data-member-position]') && !field.closest('[data-member-row]').querySelector('input[type=checkbox]').checked) continue;
      if (!field.checkValidity()) { show(index); field.reportValidity(); return false; }
    }
    return true;
  };
  const advance = () => {
    if (!validate(current)) return;
    unlocked = Math.max(unlocked, Math.min(current + 1, groups.length - 1));
    show(Math.min(current + 1, groups.length - 1), true);
  };
  groups.forEach((group, index) => group.button.addEventListener('click', () => {
    if (!wizard || index <= current || validate(current)) show(index);
  }));
  if (!wizard) nav.addEventListener('keydown', event => {
    const index = groups.findIndex(group => group.button === event.target);
    if (index < 0) return;
    const positions = {ArrowRight: (index + 1) % groups.length, ArrowLeft: (index + groups.length - 1) % groups.length, Home: 0, End: groups.length - 1};
    if (event.key in positions) {
      event.preventDefault(); show(positions[event.key]); groups[positions[event.key]].button.focus();
    }
  });
  next.addEventListener('click', advance);
  previous.addEventListener('click', () => show(Math.max(current - 1, 0), true));
  form.addEventListener('submit', event => {
    if (wizard && current !== groups.length - 1) { event.preventDefault(); advance(); return; }
    for (let index = 0; index < groups.length; index += 1) {
      if (!validate(index)) { event.preventDefault(); return; }
    }
  });
  form.querySelectorAll('.form-errors a[href^="#"]').forEach(link => link.addEventListener('click', event => {
    const id = link.getAttribute('href').slice(1);
    const field = document.getElementById(id) || (id === 'id_category' ? form.querySelector('[data-selection=single] [role=treeitem][tabindex="0"]') : null);
    const index = groups.findIndex(group => group.panel.contains(field));
    if (index >= 0) { event.preventDefault(); unlocked = Math.max(unlocked, index); show(index); field?.focus(); }
  }));
  let dirty = Boolean(form.querySelector('.form-errors'));
  const saveStatus = form.querySelector('[data-save-status]');
  if (dirty) {
    saveStatus.textContent = 'Saisie à corriger — modifications non enregistrées';
    saveStatus.classList.add('is-dirty');
  }
  form.addEventListener('input', event => {
    if (!event.target.name || ['ui_panel', 'csrfmiddlewaretoken', 'version'].includes(event.target.name)) return;
    dirty = true; saveStatus.textContent = 'Modifications non enregistrées'; saveStatus.classList.add('is-dirty');
  });
  form.addEventListener('change', event => {
    if (event.target.name && event.target.type !== 'hidden') {
      dirty = true; saveStatus.textContent = 'Modifications non enregistrées'; saveStatus.classList.add('is-dirty');
    }
  });
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    dirty = false; saveStatus.textContent = 'Enregistrement en cours…';
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  show(current);
  if (form.querySelector('.form-errors')) form.querySelector('.form-errors').focus();
});
