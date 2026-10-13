import { NavigationNode } from './content';

export function descendantIds(nodes: NavigationNode[], selected: string): Set<string> {
  const result = new Set<string>([selected]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of nodes) {
      if (node.parentId && result.has(node.parentId) && !result.has(node.id)) {
        result.add(node.id); changed = true;
      }
    }
  }
  return result;
}

export function navigationRows(nodes: NavigationNode[]): (NavigationNode & { depth: number })[] {
  const rows: (NavigationNode & { depth: number })[] = [];
  const visited = new Set<string>();
  const walk = (parentId: string | null, depth: number) => {
    for (const node of nodes.filter(n => n.parentId === parentId).sort((a, b) => a.position - b.position || a.id.localeCompare(b.id))) {
      if (visited.has(node.id)) continue;
      visited.add(node.id); rows.push({ ...node, depth }); walk(node.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

