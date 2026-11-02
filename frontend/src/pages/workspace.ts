import type { Contract } from '../api/client.js';
import { element } from '../components/dom.js';
import { metricPage, type PageDefinition } from './catalog.js';
import { mountEvaluation, type PageState } from './evaluation.js';
import { mountAutomatic, type AutomaticState } from './automatic.js';

export interface WorkspaceState extends PageState { mode?: 'automatic' | 'direct'; automatic?: AutomaticState; directMetric?: string; directStates?: Record<string, PageState> }

export function mountWorkspace(container: HTMLElement, page: PageDefinition, contract: Contract, state: WorkspaceState): () => void {
  const tabs = element('div', 'evaluation-modes');
  tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', '评测输入方式');
  const content = element('div');
  container.replaceChildren(tabs, content);
  let cleanup = () => {};
  const render = () => {
    cleanup(); tabs.replaceChildren();
    for (const [mode, label] of [['automatic', '上传文本或抽取结果'], ['direct', '高级评分参数']] as const) {
      const button = element('button', `filter-tab${(state.mode ?? 'automatic') === mode ? ' active' : ''}`, label);
      button.type = 'button'; button.setAttribute('aria-pressed', String((state.mode ?? 'automatic') === mode));
      button.addEventListener('click', () => { state.mode = mode; render(); }); tabs.append(button);
    }
    state.automatic ??= {};
    if (state.mode === 'direct' && page.metrics) {
      const metric = page.metrics.includes(state.directMetric ?? '') ? state.directMetric! : page.metrics[0]!;
      state.directMetric = metric;
      state.directStates ??= {};
      state.directStates[metric] ??= { text: JSON.stringify(contract.operations[metric]!.example, null, 2) };
      const selector = element('div', 'evaluation-modes');
      selector.setAttribute('role', 'group'); selector.setAttribute('aria-label', '选择直接评分指标');
      for (const child of page.metrics) {
        const button = element('button', `filter-tab${child === metric ? ' active' : ''}`, metricPage(child).title);
        button.type = 'button'; button.setAttribute('aria-pressed', String(child === metric));
        button.addEventListener('click', () => { state.directMetric = child; render(); }); selector.append(button);
      }
      const directContent = element('div'); content.replaceChildren(selector, directContent);
      cleanup = mountEvaluation(directContent, metricPage(metric), contract, state.directStates[metric]!);
    } else {
      cleanup = state.mode === 'direct' ? mountEvaluation(content, page, contract, state) : mountAutomatic(content, page, state.automatic);
    }
  };
  render();
  return () => cleanup();
}
