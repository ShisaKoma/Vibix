import { DEFAULT_CLIENT_OPTIONS } from './client-options';
import { SiteContent, ContentSource, ContentModule, ContentItem, CobwebItem } from './content';

export const DEFAULT_SETTINGS = {
  title: 'Le journal ViBix',
  description: 'Un espace pour partager nos idées, nos projets et les nouvelles qui nous rapprochent.',
  logoUrl: null,
  template: 'bloc' as const,
  colorScheme: 'light' as const,
  colors: { primary: ['#31594B', '#193A30'] as [string, string], secondary: ['#EAF0E9', '#F8F8F3'] as [string, string] }
};

export class MockContentSource implements ContentSource {
  async cobwebDetail(siteId: number, id: string): Promise<CobwebItem | null> {
    return (await this.load(siteId)).cobwebItems.find(item => item.id === id) ?? null;
  }
  async detail(siteId: number, module: ContentModule, id: string): Promise<ContentItem | null> {
    return (await this.load(siteId))[module].find(item => item.id === id) ?? null;
  }

  async load(_siteId: number): Promise<SiteContent> {
    return structuredClone({
      settings: DEFAULT_SETTINGS,
      clientOptions: DEFAULT_CLIENT_OPTIONS,
      cobwebGroups: [],
      cobwebItems: [],
      navigation: {
        articles: [
          { id: 'vie', parentId: null, label: 'Vie du collectif', position: 1 },
          { id: 'rencontres', parentId: 'vie', label: 'Rencontres', position: 1 },
          { id: 'projets', parentId: null, label: 'Nos projets', position: 2 },
          { id: 'vide', parentId: null, label: 'À venir', position: 3 }
        ],
        pages: [
          { id: 'collectif', parentId: null, label: 'Le collectif', position: 1 },
          { id: 'mission', parentId: 'collectif', label: 'Notre mission', position: 1 },
          { id: 'pratique', parentId: null, label: 'Informations pratiques', position: 2 }
        ]
      },
      articles: [
        { id: 'premiers-pas', title: 'Un nouveau chapitre commence', description: 'Notre espace prend forme. Découvrez les premières étapes du projet.', categoryId: 'vie', position: 3, inHome: true, contentHtml: '<p>Bienvenue dans notre espace de démonstration. Ce premier article illustre le parcours de lecture des actualités.</p><h2>Construire ensemble</h2><p>La navigation, les contenus et l’identité du site sont fournis par une source de données interchangeable.</p>' },
        { id: 'atelier', title: 'Une rencontre pour imaginer la suite', description: 'Des idées, des échanges et une envie commune de faire avancer les choses.', categoryId: 'rencontres', position: 2, inHome: true, contentHtml: '<p>Cette rencontre est fictive et sert à explorer une catégorie enfant du module Articles.</p><p>La rubrique parente « Vie du collectif » inclut également cet article.</p>' },
        { id: 'coulisses', title: 'Dans les coulisses de notre site', description: 'Une interface simple, une identité adaptable et des contenus bien organisés.', categoryId: 'projets', position: 1, inHome: false, contentHtml: '<p>Ce POC est rendu dans le navigateur avec Angular. Docker sert les fichiers statiques via Nginx.</p><p>La connexion à l’API ViBix sera ajoutée après stabilisation de son contrat.</p>' }
      ],
      pages: [
        { id: 'a-propos', title: 'Un collectif, des idées communes', description: 'Découvrez qui nous sommes et ce qui nous anime.', categoryId: 'collectif', position: 2, contentHtml: '<p>Cette page fictive présente le collectif et illustre une page éditoriale.</p><h2>Un espace ouvert</h2><p>Nous partageons ici nos projets, nos actualités et nos informations pratiques.</p>' },
        { id: 'notre-mission', title: 'Notre mission', description: 'Créer des liens et donner aux idées un espace pour grandir.', categoryId: 'mission', position: 1, contentHtml: '<p>Cette page appartient à une rubrique enfant. Sa position et son rattachement sont définis par la source de démonstration.</p>' },
        { id: 'contact', title: 'Restons en contact', description: 'Toutes les informations utiles au même endroit.', categoryId: 'pratique', position: 1, contentHtml: '<p>Page de démonstration sans coordonnées réelles ni formulaire connecté.</p>' }
      ]
    });
  }
}
