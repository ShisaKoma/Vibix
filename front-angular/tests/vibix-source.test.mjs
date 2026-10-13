import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const optionsCode = ts.transpileModule(await readFile(new URL('../src/app/client-options.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 }
}).outputText;
const optionsUrl = `data:text/javascript;base64,${Buffer.from(optionsCode).toString('base64')}`;
const code = ts.transpileModule(await readFile(new URL('../src/app/vibix-source.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 }
}).outputText.replace("'./client-options'", JSON.stringify(optionsUrl));
const { VibixContentSource } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const config = { siteId: 2, dataMode: 'api', apiBase: '/api/v1/public' };
const identity = { id: 2, name: 'Site fictif de test', description: 'Description', colors: { primary: ['#123456', '#234567'], secondary: ['#345678', '#456789'] } };
const item = id => ({ id, title: `Contenu ${id}`, description: '', content_html: '<p>Publié</p>', category_id: 7, group_id: 7, position: 1, in_home: true });
const page = (results = [], current = 1, pages = 1, navigation = []) => ({ count: results.length, page: current, pages, results, navigation });
async function withFetch(handler, action, withCobweb = false) {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    if (url.includes('/cobweb/') && !withCobweb) return json(page());
    return handler(url, options);
  };
  try { await action(new VibixContentSource(config)); }
  finally { globalThis.fetch = original; }
}
const json = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

test('maps identity, includes later API pages, and distinguishes group/category IDs', async () => {
  const navigation = [
    { id: 7, parent_id: null, label: 'Catégorie', position: 1, field: 'pages_page.category' },
    { id: 7, parent_id: null, label: 'Groupe', position: 2, field: 'pages_page.group' }
  ];
  const requested = [];
  await withFetch(url => {
    requested.push(url);
    if (url.endsWith('/sites/2/')) return json(identity);
    if (url.endsWith('/articles/')) return json(page([item(1)], 1, 2));
    if (url.endsWith('/articles/?page=2')) return json(page([item(2)], 2, 2));
    return json(page([item(3)], 1, 1, navigation));
  }, async source => {
    const result = await source.load(2);
    assert.equal(result.settings.title, identity.name);
    assert.equal(result.settings.logoUrl, null);
    assert.deepEqual(result.articles.map(row => row.id), ['1', '2']);
    assert.deepEqual(result.navigation.pages.map(row => row.id), ['category:7', 'group:7']);
    assert.equal(result.pages[0].groupId, 'group:7');
    assert(requested.includes('/api/v1/public/sites/2/articles/?page=2'));
  });
});

test('keeps genuinely empty API collections empty', async () => {
  await withFetch(url => json(url.endsWith('/sites/2/') ? identity : page()), async source => {
    const result = await source.load(2);
    assert.deepEqual(result.articles, []);
    assert.deepEqual(result.pages, []);
  });
});

test('rejects invalid palette from the public API', async () => {
  await withFetch(url => json(url.endsWith('/sites/2/')
    ? { ...identity, colors: { primary: ['red', '#123456'], secondary: ['#123456', '#123456'] } } : page()),
    async source => assert.rejects(() => source.load(2), /contrat public/));
});

test('rejects oversized collections instead of silently truncating', async () => {
  await withFetch(url => json(url.endsWith('/sites/2/') ? identity : page([], 1, 101)),
    async source => assert.rejects(() => source.load(2), /100 pages/));
});

test('detail rechecks publication and reports 404 without substituting demo content', async () => {
  await withFetch(() => new Response('{}', { status: 404 }), async source => {
    await assert.rejects(() => source.detail(2, 'articles', '1'), /pas disponible/);
    assert.equal(await source.detail(2, 'articles', 'missing'), null);
  });
});

test('reports unavailable API instead of falling back to mock', async () => {
  await withFetch(() => { throw new Error('offline'); },
    async source => assert.rejects(() => source.load(2), /inaccessible/));
});

test('loads Cobweb groups, partner classification, member pagination and safe images', async () => {
  const asset = { id: 4, url: '/api/v1/public/sites/2/media/4/image/', alt_text: 'Image fictive', width: 600, height: 300 };
  const group = { id: 9, title: 'Partenaires', content_html: '<p>Composition</p>', position: 1, presentation: 'cards', members_url: '/api/v1/public/sites/2/cobweb/groups/9/members/' };
  const member = position => ({ position, item: { id: position, title: `Partenaire ${position}`, description: '', content_html: '<p>Fictif</p>', kind: 'partner', link: 'https://example.com', picture: asset, thumbnail: null } });
  await withFetch(url => {
    if (url.endsWith('/sites/2/')) return json(identity);
    if (url.includes('/9/members/')) return json(url.includes('page=2') ? page([member(1)], 2, 2) : page([member(2)], 1, 2));
    if (url.includes('/cobweb/groups/')) return json(page([group]));
    return json(page());
  }, async source => {
    const result = await source.load(2);
    assert.equal(result.cobwebGroups[0].partners, true);
    assert.deepEqual(result.cobwebGroups[0].members.map(member => member.position), [1, 2]);
    assert.equal(result.cobwebGroups[0].members[0].item.picture.url, asset.url);
  }, true);
});

test('rejects images outside the selected site public media route', async () => {
  await withFetch(url => json(url.endsWith('/sites/2/') ? identity : page([{
    ...item(1), picture: { id: 4, url: '/api/v1/public/sites/3/media/4/image/', alt_text: 'Autre site', width: 600, height: 300 }
  }])), async source => assert.rejects(() => source.load(2), /périmètre public/));
});


test('maps active client options without inventing missing server choices', async () => {
  const frontend_options = { templates: [{value: 'shape', label: 'Organique'}], color_schemes: [] };
  await withFetch(url => json(url.endsWith('/sites/2/') ? {...identity, frontend_options} : page()), async source => {
    const result = await source.load(2);
    assert.deepEqual(result.clientOptions, {templates: frontend_options.templates, colorSchemes: []});
    assert.equal(result.settings.template, 'bloc');
    assert.equal(result.settings.colorScheme, 'light');
  });
});

test('rejects malformed client options instead of applying arbitrary configuration', async () => {
  const frontend_options = { templates: [{value: 'shape; color:red', label: 'Invalide'}], color_schemes: [] };
  await withFetch(url => json(url.endsWith('/sites/2/') ? {...identity, frontend_options} : page()),
    async source => assert.rejects(() => source.load(2), /choix du front/));
});

test('maps editorial galleries and standalone Cobweb resources, then rechecks their detail', async () => {
  const asset = { id: 11, url: '/api/v1/public/sites/2/media/11/image/', alt_text: 'Visuel public', width: 640, height: 360 };
  const resource = { id: 8, title: 'Lien importé', description: 'Ressource', content_html: '<p>Texte</p>', category_id: 5, position: 2, kind: 'link', link: 'https://example.com', picture: asset, thumbnail: null };
  await withFetch(url => {
    if (url.endsWith('/sites/2/')) return json(identity);
    if (url.endsWith('/articles/')) return json(page([{ ...item(3), gallery: [{position: 2, asset}] }]));
    if (url.endsWith('/cobweb/8/')) return json(resource);
    if (url.includes('/cobweb/') && !url.includes('/groups/')) return json(page([resource]));
    return json(page());
  }, async source => {
    const result = await source.load(2);
    assert.equal(result.articles[0].gallery[0].asset.url, asset.url);
    assert.equal(result.cobwebItems[0].title, resource.title);
    assert.equal(result.cobwebItems[0].categoryId, 'category:5');
    assert.equal((await source.cobwebDetail(2, '8')).id, '8');
    assert.equal(await source.cobwebDetail(2, 'wrong'), null);
  }, true);
});
