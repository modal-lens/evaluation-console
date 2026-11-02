export type ResultKind = 'extraction' | 'fusion' | 'domain' | 'resolution' | 'classification' | 'community';
export interface PageDefinition { slug: string; title: string; group: string; kind: ResultKind; description: string; rule: string; icon: string; metrics?: string[] }

export const legacyPages: PageDefinition[] = [
  { slug: 'entity-extraction', title: '实体抽取', group: '抽取评测', kind: 'extraction', icon: 'E', description: '衡量实体名称与类型识别的完整性和准确性。', rule: '按样本隔离，以实体名称和类型精确匹配。同一样本内重复实体只计一次；不按字符跨度评分。' },
  { slug: 'relation-extraction', title: '关系抽取', group: '抽取评测', kind: 'extraction', icon: 'R', description: '检验有向关系三元组的抽取质量。', rule: '预测与标注必须使用同一实体 ID。按头实体、关系类型、尾实体精确匹配，关系方向参与计分。' },
  { slug: 'domain-entity-extraction', title: '专业领域实体抽取', group: '抽取评测', kind: 'domain', icon: 'D', description: '对比不提供类型列表和引入领域类型列表后的实体、关系 F1。', rule: '文本模式运行开放抽取与配置抽取两组实验；两组使用同一份完整实体及关系标注。已有结果需提供引入后 documents 和引入前 baseline_documents，才能计算两种 F1 差值。' },
  ...(['cross-document', 'cross-language'] as const).flatMap(scope => (['entity', 'relation', 'attribute'] as const).map((kind, index) => {
    const scopeName = scope === 'cross-document' ? '跨文档' : '跨语种';
    const kindName = ['实体', '关系', '属性'][index]!;
    return { slug: `${scope}/${kind}-fusion`, title: `${kindName}消融`, group: `${scopeName}消融`, kind: 'fusion' as const, icon: ['E', 'R', 'A'][index]!, description: `评价${scopeName}${kindName}的合并与对齐质量。`, rule: `此处消融指合并与对齐，按对象对计算 F1。仅统计${scope === 'cross-document' ? 'document_id 不同' : 'language 不同'}的对象对；簇编号无需与标注一致。最多 1000 个对象，null 表示未合并。` };
  })),
  { slug: 'spatiotemporal-resolution', title: '多粒度时空消歧', group: '综合评测', kind: 'resolution', icon: 'T', description: '从时间和空间两个维度检查标准化结果。', rule: '预测和人工标注使用相同粒度的标准化值，按字符串精确比较。time 为时间，space 为空间；null 按错误计分。' },
  { slug: 'multilabel-classification', title: '多标签分类', group: '综合评测', kind: 'classification', icon: 'L', description: '同时观察总体、标签平均与逐标签表现。', rule: '标签使用统一字符串 ID；重复标签会去重。Macro F1 按预测与标注标签并集计算；整组准确率要求所有标签完全一致。' },
  { slug: 'community-discovery', title: '社区网络发现', group: '综合评测', kind: 'community', icon: 'C', description: '评估实体网络中的社区分组是否准确。', rule: '先对预测与标注社区做最优一一匹配，再计算正确节点比例。每个节点只属于一个社区，null 表示未分配。最多 5000 个节点、200 个社区标签。' },
];

export const pages: PageDefinition[] = [
  { slug: 'entity-relation-extraction', title: '实体与关系抽取', group: '抽取评测', kind: 'extraction', icon: 'ER', description: '一次上传和抽取，同时评测实体识别与有向关系质量。', rule: '实体按名称和类型匹配，关系按端点语义、关系类型和方向匹配；两项指标分别计算。', metrics: ['entity-extraction', 'relation-extraction'] },
  legacyPages.find(page => page.slug === 'domain-entity-extraction')!,
  ...(['cross-document', 'cross-language'] as const).map(scope => {
    const scopeName = scope === 'cross-document' ? '跨文档' : '跨语种';
    return { slug: `${scope}/fusion`, title: `${scopeName}消融`, group: `${scopeName}消融`, kind: 'fusion' as const, icon: scope === 'cross-document' ? 'D' : 'L', description: '共用一次抽取与融合，分别输出实体、关系、属性三项 F1。', rule: `按对象对分别评分，仅比较${scope === 'cross-document' ? '不同文档' : '不同语言'}的对象；三类对象均需完整标注 cluster，包含不应合并的对象。`, metrics: ['entity', 'relation', 'attribute'].map(kind => `${scope}/${kind}-fusion`) };
  }),
  ...legacyPages.filter(page => ['spatiotemporal-resolution', 'multilabel-classification', 'community-discovery'].includes(page.slug)),
];

export const metricPage = (slug: string): PageDefinition => legacyPages.find(page => page.slug === slug)!;
export const modulePage = (slug: string): PageDefinition | undefined => pages.find(page => page.slug === slug || page.metrics?.includes(slug));

export const groups = [...new Set(pages.map(page => page.group))];
export const pagePath = (page: PageDefinition): string => `/evaluation/${page.slug}`;
