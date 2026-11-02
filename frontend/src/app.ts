import type { Contract } from './api/client.js';
import { element, message } from './components/dom.js';
import { groups, pagePath, pages, legacyPages, modulePage, type PageDefinition } from './pages/catalog.js';
import { mountWorkspace as mountEvaluation, type WorkspaceState as PageState } from './pages/workspace.js';

const root = document.querySelector<HTMLElement>('#app')!;
const states = new Map<string, PageState>();
let cleanup = () => {};
let currentPath = '';
let selectedGroup = '全部评测';
let connection = 'checking';

function pageName(page: PageDefinition): string { return page.group.includes('消融') && !page.metrics ? `${page.group.replace('消融', '')}${page.title}` : page.title; }

const hasResult = (state?: PageState): boolean => !!state && !!(state.result || state.automatic?.report || Object.values(state.directStates ?? {}).some(child => child.result));
const moduleCompleted = (page: PageDefinition): boolean => hasResult(states.get(page.slug)) || (page.metrics ?? []).some(metric => hasResult(states.get(metric)));

function home(main: HTMLElement): void {
  main.innerHTML = `
    <section class="home-hero"><div class="hero-copy"><div class="eyebrow">QUALITY STARTS WITH MEASUREMENT</div><h1>让算法表现，<br><span>有据可依。</span></h1><p>从抽取到理解，从单篇到跨语种。<br>以统一的评测口径，掌握每一次算法迭代的质量。</p><a class="button primary-button" data-route href="/evaluation/entity-relation-extraction">开始第一次评测 <span aria-hidden="true">↗</span></a></div><div class="hero-chart" aria-hidden="true"><div class="chart-label"><span>评测维度</span><span>MEASURE · COMPARE · IMPROVE</span></div><div class="chart-bars"><div style="--height:42%"><span>实体</span></div><div style="--height:64%"><span>关系</span></div><div style="--height:54%"><span>消融</span></div><div style="--height:83%"><span>分类</span></div><div style="--height:70%"><span>发现</span></div></div><p>示意图 · 非实际评测结果</p></div></section>
    <div class="overview-stats"><div><span class="stat-number">7<span>个</span></span><p>评测模块 · 保留 12 项指标</p></div><div><span class="stat-number">04<span>组</span></span><p>覆盖算法关键环节</p></div><div><span class="stat-number completed-count">0<span>/ 7</span></span><p>本次会话已评测</p></div><div class="stat-note"><span class="mini-dot"></span><p>基于真实预测与人工标注<br><strong>结果由后端接口计算</strong></p></div></div>
    <section class="catalog"><div class="section-heading"><div><div class="eyebrow">EVALUATION SUITE</div><h2>选择一项评测</h2></div><p>每一项指标，都对应一个可执行的评测。</p></div><div class="filter-tabs" role="group" aria-label="按评测类别筛选"></div><div class="catalog-grid"></div></section>
    <div class="home-footnote"><span>i</span><p>上传原文与人工标注，由现有算法自动生成预测并评测；也可切换到直接评分，使用已有预测结果。</p><a href="/docs" target="_blank" rel="noopener">接口文档 ↗</a></div>`;
  const tabs = main.querySelector<HTMLElement>('.filter-tabs')!;
  const grid = main.querySelector<HTMLElement>('.catalog-grid')!;
  const renderCatalog = () => {
    grid.replaceChildren();
    tabs.replaceChildren();
    for (const group of ['全部评测', ...groups]) {
      const button = element('button', `filter-tab${selectedGroup === group ? ' active' : ''}`, group);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(selectedGroup === group));
      button.addEventListener('click', () => { selectedGroup = group; renderCatalog(); });
      tabs.append(button);
    }
    for (const page of pages.filter(page => selectedGroup === '全部评测' || page.group === selectedGroup)) {
      const link = element('a', 'catalog-card');
      link.href = pagePath(page);
      link.dataset.route = '';
      const upper = element('div', 'card-upper');
      upper.append(element('span', `page-icon kind-${page.kind}`, page.icon), element('span', 'card-group', page.group));
      link.append(upper, element('h3', '', pageName(page)), element('p', '', page.description));
      const footer = element('div', 'card-footer');
      footer.append(element('span', '', page.kind === 'community' || page.kind === 'resolution' ? '准确率评测' : 'F1 质量评测'), element('span', 'card-arrow', '↗'));
      link.append(footer);
      if (moduleCompleted(page)) link.append(element('span', 'card-completed', '已评测 ✓'));
      grid.append(link);
    }
  };
  renderCatalog();
  const completed = pages.filter(moduleCompleted).length;
  main.querySelector('.completed-count')!.innerHTML = `${completed}<span>/ 7</span>`;
}

async function boot(): Promise<void> {
  const response = await fetch('/src/data/contract.json');
  if (!response.ok) throw new Error('无法加载接口参数定义，请重新构建前端。');
  const contract = await response.json() as Contract;
  if (!contract.operations || legacyPages.some(page => !contract.operations[page.slug])) throw new Error('接口定义不完整，请重新导出契约并构建。');
  root.innerHTML = `
    <aside class="sidebar" aria-label="主导航"><a class="brand" data-route href="/"><span class="brand-mark"><i></i><i></i><i></i></span><div>智析<span>算法评测工作台</span></div></a><div class="nav-label">WORKSPACE</div><a href="/" class="nav-home nav-link" data-route><span class="nav-symbol">▦</span>评测概览</a><nav></nav><div class="sidebar-bottom"><div class="sidebar-note"><span class="note-mark">↗</span><strong>让每一次迭代<br>都看得见进步。</strong><p>统一标准 · 真实评测</p></div><a href="/docs" target="_blank" rel="noopener" class="docs-link">接口文档 <span>↗</span></a><div class="version">ZNCL · EVALUATION CONSOLE <span>v1.0</span></div></div></aside>
    <div class="main-shell"><header class="topbar"><button class="menu-toggle" type="button" aria-label="展开导航" aria-expanded="false">☰</button><div class="breadcrumb">工作空间 <span>/</span> <strong>评测概览</strong></div><div class="topbar-right"><button type="button" class="connection-badge" aria-label="检查后端连接"><span class="connection-dot"></span><span class="connection-text">检查连接中</span></button><span class="topbar-divider"></span><span class="workspace-avatar" aria-hidden="true">智</span></div></header><main id="main" tabindex="-1"></main><footer class="site-footer"><span>智析 · 智能文本处理评测平台</span><span>MEASURE WHAT MATTERS.</span></footer></div>`;
  const main = root.querySelector<HTMLElement>('main')!;
  const nav = root.querySelector('nav')!;
  for (const group of groups) {
    const section = element('div', 'nav-group');
    section.append(element('div', 'nav-group-label', group));
    for (const page of pages.filter(page => page.group === group)) {
      const link = element('a', 'nav-link');
      link.href = pagePath(page);
      link.dataset.route = '';
      link.append(element('span', 'nav-symbol', page.icon), document.createTextNode(page.title));
      section.append(link);
    }
    nav.append(section);
  }

  const render = () => {
    cleanup();
    cleanup = () => {};
    currentPath = location.pathname.replace(/\/$/, '') || '/';
    const page = [...pages, ...legacyPages].find(page => pagePath(page) === currentPath);
    for (const link of root.querySelectorAll<HTMLAnchorElement>('.sidebar a[data-route]')) {
      const active = link.pathname === currentPath || (!!page && link.pathname === pagePath(modulePage(page.slug)!));
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    root.querySelector('.breadcrumb strong')!.textContent = page ? pageName(page) : currentPath === '/' ? '评测概览' : '页面不存在';
    document.title = `${page ? pageName(page) : '评测概览'} · 智析`;
    root.classList.remove('nav-open');
    root.querySelector('.menu-toggle')!.setAttribute('aria-expanded', 'false');
    if (currentPath === '/') home(main);
    else if (page) {
      let state = states.get(page.slug);
      if (!state) { state = { text: JSON.stringify(contract.operations[page.metrics?.[0] ?? page.slug]!.example, null, 2) }; states.set(page.slug, state); }
      cleanup = mountEvaluation(main, page, contract, state);
    } else {
      main.innerHTML = '<section class="not-found"><div class="eyebrow">404</div><h1>没有找到这个页面</h1><p>请从侧栏选择评测项目，或回到概览。</p><a class="button primary-button" href="/" data-route>返回评测概览</a></section>';
    }
  };
  const navigate = (path: string) => { if (path !== currentPath) { history.pushState({}, '', path); render(); window.scrollTo(0, 0); main.focus({ preventScroll: true }); } };
  document.addEventListener('click', event => {
    if (!(event instanceof MouseEvent) || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element).closest<HTMLAnchorElement>('a[data-route]');
    if (!anchor || anchor.origin !== location.origin) return;
    event.preventDefault();
    navigate(anchor.pathname);
  });
  window.addEventListener('popstate', render);
  root.querySelector('.menu-toggle')!.addEventListener('click', () => {
    const open = root.classList.toggle('nav-open');
    root.querySelector('.menu-toggle')!.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { root.classList.remove('nav-open'); root.querySelector('.menu-toggle')!.setAttribute('aria-expanded', 'false'); } });

  const checkConnection = async () => {
    if (connection === 'pending') return;
    connection = 'pending';
    const badge = root.querySelector<HTMLElement>('.connection-badge')!;
    badge.className = 'connection-badge';
    root.querySelector('.connection-text')!.textContent = '检查连接中';
    try {
      const health = await fetch('/health', { signal: AbortSignal.timeout(4000), cache: 'no-store' });
      const body = await health.json() as { status?: string };
      if (!health.ok || body.status !== 'ok') throw new Error('服务未就绪');
      connection = 'online';
      badge.classList.add('online');
      root.querySelector('.connection-text')!.textContent = '后端已连接';
      badge.title = 'HTTP 服务可达；点击重新检查。具体接口是否可用以评测请求为准。';
    } catch {
      connection = 'offline';
      badge.classList.add('offline');
      root.querySelector('.connection-text')!.textContent = '后端暂未连通';
      badge.title = '点击重新检查连接。请确认后端已启动且代理地址正确。';
    }
  };
  root.querySelector('.connection-badge')!.addEventListener('click', checkConnection);
  render();
  void checkConnection();
}

boot().catch(error => {
  const box = element('section', 'boot-error');
  box.append(element('h1', '', '工作台暂时无法加载'), element('p', '', message(error)));
  const button = element('button', 'button primary-button', '重新加载');
  button.addEventListener('click', () => location.reload());
  box.append(button);
  root.replaceChildren(box);
});
