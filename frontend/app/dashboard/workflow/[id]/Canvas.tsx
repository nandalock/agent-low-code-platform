'use client';

import { useCallback, useEffect, useImperativeHandle, forwardRef, useState } from 'react';
import {
  ReactFlow, Background, MiniMap, ReactFlowProvider,
  addEdge, useNodesState, useEdgesState,
  type Connection, type Node, type Edge,
} from '@xyflow/react';
import { Plus, User, GitBranch, Brain, X, ChevronDown, ChevronRight } from 'lucide-react';
import { T, S } from '@/app/theme';
import { CustomNode, type CustomNodeData } from './nodes/CustomNode';
import { CustomEdge } from './edges/CustomEdge';
import CachePolicyEditor, { type CachePolicyData } from '@/app/agents/_components/CachePolicyEditor';
import '@xyflow/react/dist/style.css';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const nodeTypes = { custom: CustomNode };
const edgeTypes = { custom: CustomEdge };

let nodeId = 0;
function nextId() { nodeId++; return `node_${nodeId}`; }

const INITIAL_NODES: Node<CustomNodeData>[] = [
  { id:'start', type:'custom', position:{x:80, y:200},  data:{ nodeType:'start', label:'开始', description:'用户消息输入' } },
  { id:'end',   type:'custom', position:{x:800, y:200}, data:{ nodeType:'end',   label:'结束', description:'返回最终结果' } },
];

const ADDABLE: Record<string, { label:string; desc:string; icon:React.ReactNode }> = {
  agent:     { label:'LLM Agent',   desc:'调用 LLM 处理用户请求',  icon:<User size={14} color={T.accent} /> },
  router:    { label:'意图路由',    desc:'三层意图识别，按意图分发', icon:<Brain size={14} color="#9333EA" /> },
  condition: { label:'条件分支',    desc:'根据条件分支路由',       icon:<GitBranch size={14} color="#FF7D00" /> },
};

export interface CanvasRef {
  getData: () => { nodes: Node<CustomNodeData>[]; edges: Edge[] };
}

interface Props {
  workflowId: string;
}

const fieldLabel: React.CSSProperties = { fontSize: 11, fontWeight: 500, color: T.tertiary, display: 'flex', flexDirection: 'column', gap: 4 };
const selectStyle: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: `1px solid ${T.border}`, fontSize: 12, color: T.text, outline: 'none', fontFamily: 'inherit', background: T.surface };
const inputStyle: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: `1px solid ${T.border}`, fontSize: 12, color: T.text, outline: 'none', fontFamily: 'inherit' };
const rangeHint: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: T.tertiary, marginTop: -4 };
const sectionTitle: React.CSSProperties = { fontSize: 10, fontWeight: 600, color: T.tertiary, letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: S.sm };

function CacheSettingsPanel({ selectedNode, agentList, cacheExpanded, setCacheExpanded, updateNodeConfig }: {
  selectedNode: Node<CustomNodeData>;
  agentList: { key: string; name: string; agent_type: string; routable?: string[]; cache_policy?: any }[];
  cacheExpanded: boolean;
  setCacheExpanded: React.Dispatch<React.SetStateAction<boolean>>;
  updateNodeConfig: (nodeId: string, config: any) => void;
}) {
  const agentKey = selectedNode.data.config?.agent_key || '';
  const agentInfo = agentList.find(a => a.key === agentKey);
  const agentPolicy = agentInfo?.cache_policy;
  const cacheEnabled = selectedNode.data.config?.cache?.enabled || false;
  const baseScore = agentPolicy?.base_score;
  const affinityLabel =
    baseScore === undefined ? null :
    baseScore >= 0.85 ? { text: '非常适合', color: T.success, bg: '#E8FFEA' } :
    baseScore >= 0.50 ? { text: '一般', color: '#B8860B', bg: '#FFF8E1' } :
    baseScore > 0.00 ? { text: '不适合', color: '#C62828', bg: '#FFEBEE' } :
    { text: '永不缓存', color: '#C62828', bg: '#FFEBEE' };

  const curCache: Record<string, any> = selectedNode.data.config?.cache || {};
  function upd(patch: Record<string, any>) {
    updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, cache: { ...curCache, ...patch } });
  }

  return (
    <div style={{ marginTop: S.xs, borderTop: `1px solid ${T.border}`, paddingTop: S.md }}>
      <button
        onClick={() => setCacheExpanded(v => !v)}
        style={{ display: 'flex', alignItems: 'center', gap: 4, padding: 0, border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 12, fontWeight: 500, color: T.secondary, fontFamily: 'inherit' }}
      >
        {cacheExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        缓存设置
        {cacheEnabled && <span style={{ marginLeft: 6, fontSize: 10, color: T.success, background: '#E8FFEA', padding: '1px 6px', borderRadius: 8 }}>开</span>}
      </button>

      {cacheExpanded && (
        <div style={{ marginTop: S.sm, display: 'flex', flexDirection: 'column', gap: S.sm }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12, fontWeight: 500, color: T.text }}>
            <input type="checkbox" checked={cacheEnabled}
              onChange={e => {
                if (e.target.checked && Object.keys(curCache).length === 0) {
                  upd({ enabled: true, strategy: 'semantic', threshold: 0.90, ttl: 604800, min_score: 0.40, semantic_min_score: 0.70, min_answer_length: 50, max_question_length: 10000 });
                } else {
                  upd({ enabled: e.target.checked });
                }
              }}
              style={{ accentColor: T.accent }} />
            启用缓存
          </label>

          {!agentKey && <div style={{ padding: '5px 8px', borderRadius: 6, fontSize: 11, background: '#F5F5F5', color: T.tertiary }}>请先选择 Agent 以配置缓存策略</div>}

          {cacheEnabled && agentKey && (
            <>
              <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: S.sm }}>
                <div style={sectionTitle}>匹配策略</div>
              </div>

              <label style={fieldLabel}>缓存模式</label>
              <select value={selectedNode.data.config?.cache?.strategy || 'semantic'}
                onChange={e => upd({ strategy: e.target.value })}
                style={selectStyle}>
                <option value="exact">精确匹配 (L1) — 仅命中完全一致的提问</option>
                <option value="semantic">语义匹配 (L1+L2) — 支持语义相似匹配</option>
              </select>

              <label style={fieldLabel}>语义相似度阈值: {selectedNode.data.config?.cache?.threshold ?? 0.90}</label>
              <input type="range" min="0.60" max="1.00" step="0.01"
                value={selectedNode.data.config?.cache?.threshold ?? 0.90}
                onChange={e => upd({ threshold: parseFloat(e.target.value) })}
                style={{ accentColor: T.accent }} />
              <div style={rangeHint}><span>0.60 宽松</span><span>1.00 严格</span></div>

              <label style={fieldLabel}>TTL 过期时间</label>
              <select value={selectedNode.data.config?.cache?.ttl ?? 604800}
                onChange={e => upd({ ttl: parseInt(e.target.value) })}
                style={selectStyle}>
                <option value={600}>10 分钟</option>
                <option value={3600}>1 小时</option>
                <option value={86400}>1 天</option>
                <option value={259200}>3 天</option>
                <option value={604800}>7 天</option>
                <option value={2592000}>30 天</option>
              </select>

              <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: S.sm, marginTop: 4 }}>
                <div style={sectionTitle}>质量门槛</div>
              </div>

              <label style={fieldLabel}>最低综合评分: {selectedNode.data.config?.cache?.min_score ?? 0.40}</label>
              <input type="range" min="0.00" max="1.00" step="0.05"
                value={selectedNode.data.config?.cache?.min_score ?? 0.40}
                onChange={e => upd({ min_score: parseFloat(e.target.value) })}
                style={{ accentColor: T.accent }} />
              <div style={rangeHint}><span>0.00 全缓存</span><span>1.00 仅高分</span></div>
              <div style={{ padding: '4px 8px', borderRadius: 4, fontSize: 10, background: '#F7F8FA', color: T.tertiary, border: `1px solid ${T.border}`, marginTop: -2, marginBottom: 4 }}>
                低于此门槛：不写入缓存。达到此门槛但低于语义门槛：写入 L1 精确缓存。达到语义门槛：写入 L1 + L2 语义索引。
              </div>

              <label style={fieldLabel}>语义缓存门槛: {selectedNode.data.config?.cache?.semantic_min_score ?? 0.70}</label>
              <input type="range" min="0.40" max="1.00" step="0.05"
                value={selectedNode.data.config?.cache?.semantic_min_score ?? 0.70}
                onChange={e => upd({ semantic_min_score: parseFloat(e.target.value) })}
                style={{ accentColor: '#9333EA' }} />
              <div style={rangeHint}><span>0.40 宽松（更多语义命中）</span><span>1.00 严格（仅高质量进 L2）</span></div>

              <div style={{ display: 'flex', gap: S.sm }}>
                <label style={{ ...fieldLabel, flex: 1 }}>
                  最短回复长度 (字符)
                  <input type="number" min={1} max={10000}
                    value={selectedNode.data.config?.cache?.min_answer_length ?? 50}
                    onChange={e => upd({ min_answer_length: parseInt(e.target.value) || 50 })}
                    style={inputStyle} />
                </label>
                <label style={{ ...fieldLabel, flex: 1 }}>
                  最长提问长度 (字符)
                  <input type="number" min={50} max={50000}
                    value={selectedNode.data.config?.cache?.max_question_length ?? 10000}
                    onChange={e => upd({ max_question_length: parseInt(e.target.value) || 10000 })}
                    style={inputStyle} />
                </label>
              </div>

              <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: S.sm, marginTop: 4 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: S.sm }}>
                  <span style={sectionTitle}>策略覆盖</span>
                  {agentPolicy && <a href={`/agents/${agentKey}`} target="_blank" style={{ fontSize: 10, color: T.accent, textDecoration: 'none' }}>Agent 页 →</a>}
                </div>
                <div style={{ padding: '4px 8px', borderRadius: 4, fontSize: 10, background: '#F7F8FA', color: T.tertiary, border: `1px solid ${T.border}`, marginBottom: S.sm }}>
                  以下字段为空时沿用 Agent 定义的对应值。在此配置的值会覆盖 Agent 定义。
                  {affinityLabel && <span style={{ display: 'block', marginTop: 2 }}>当前 Agent 值: <span style={{ color: affinityLabel.color, fontWeight: 500 }}>{affinityLabel.text}</span> (base_score={baseScore?.toFixed(2)})</span>}
                </div>
              </div>

              <CachePolicyEditor
                value={{
                  base_score: curCache.base_score ?? agentPolicy?.base_score ?? 0.50,
                  cacheable_intents: curCache.cacheable_intents ?? agentPolicy?.cacheable_intents ?? [],
                  block_entities: curCache.block_entities ?? agentPolicy?.block_entities ?? [],
                  content_hint: curCache.content_hint ?? agentPolicy?.content_hint ?? '',
                  scorer_weights: curCache.scorer_weights ?? agentPolicy?.scorer_weights ?? undefined,
                }}
                onChange={(p: CachePolicyData) => {
                  const overrides: Record<string, any> = {};
                  for (const field of ['base_score', 'cacheable_intents', 'block_entities', 'content_hint', 'scorer_weights'] as const) {
                    const v = p[field as keyof CachePolicyData];
                    if (v !== undefined) overrides[field] = v;
                  }
                  upd(overrides);
                }}
                minScore={curCache.min_score ?? 0.40}
                semanticMinScore={curCache.semantic_min_score ?? 0.70}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

const CanvasInner = forwardRef(function CanvasInner({ workflowId }: Props, ref: React.Ref<CanvasRef>) {
  const [nodes, setNodes, onNodesChange] = useNodesState(INITIAL_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [cacheExpanded, setCacheExpanded] = useState(false);

  // Node config panel
  const [selectedNode, setSelectedNode] = useState<Node<CustomNodeData> | null>(null);
  const [agentList, setAgentList] = useState<{ key: string; name: string; agent_type: string; routable?: string[]; cache_policy?: any }[]>([]);

  // Load from API
  useEffect(() => {
    if (workflowId === 'new') { setLoaded(true); return; }
    (async () => {
      try {
        const r = await fetch(`${API}/api/workflows/${workflowId}`);
        if (!r.ok) { setLoaded(true); return; }
        const w = await r.json();
        if (w.nodes && w.nodes.length > 0) setNodes(w.nodes);
        if (w.edges && w.edges.length > 0) setEdges(w.edges);
        const maxNum = (w.nodes || []).reduce((max: number, n: any) => {
          const m = n.id?.match(/^node_(\d+)$/);
          return m ? Math.max(max, parseInt(m[1])) : max;
        }, 0);
        nodeId = maxNum;
      } catch {}
      setLoaded(true);
    })();
  }, [workflowId, setNodes, setEdges]);

  // Load agent list for dropdown
  useEffect(() => {
    fetch(`${API}/api/agents`).then(r => r.json()).then(list => {
      setAgentList((Array.isArray(list) ? list : []).map((a: any) => ({ key: a.key, name: a.name || a.key, agent_type: a.agent_type || 'agent', routable: a.routable || [], cache_policy: a.cache_policy })));
    }).catch(() => {});
  }, []);

  useImperativeHandle(ref, () => ({
    getData: () => ({ nodes, edges }),
  }), [nodes, edges]);

  const onConnect = useCallback((conn: Connection) => {
    setEdges(eds => addEdge({ ...conn, type:'custom' }, eds));
  }, [setEdges]);

  const addNode = useCallback((type: string) => {
    const def = ADDABLE[type];
    const newNode: Node<CustomNodeData> = {
      id: nextId(), type:'custom',
      position: { x:380, y:150 + nodes.length * 60 },
      data: {
        nodeType: type as CustomNodeData['nodeType'], label: def.label, description: def.desc,
        config: type === 'agent' ? { agent_key: agentList.find(a => a.agent_type === 'agent')?.key || '' } : type === 'router' ? { agent_key: 'router' } : { branches: [], field: '', op: 'contains', value: '' },
      },
    };
    setNodes(nds => [...nds, newNode]);
    setMenuOpen(false);
  }, [nodes.length, setNodes, agentList]);

  const onSelectionChange = useCallback(({ nodes: sel }: { nodes: Node<CustomNodeData>[] }) => {
    if (sel.length === 1) {
      const n = sel[0];
      if (n.data.nodeType !== 'start' && n.data.nodeType !== 'end') {
        setSelectedNode(n);
        return;
      }
    }
    setSelectedNode(null);
  }, []);

  const updateNodeConfig = useCallback((nodeId: string, config: CustomNodeData['config']) => {
    setNodes(nds => nds.map(n =>
      n.id === nodeId ? { ...n, data: { ...n.data, config } } : n
    ));
    setSelectedNode(prev => prev && prev.id === nodeId ? { ...prev, data: { ...prev.data, config } } : prev);
  }, [setNodes]);

  if (!loaded) {
    return <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100%', color:T.secondary, fontSize:14 }}>加载中...</div>;
  }

  return (
    <div style={{ width:'100%', height:'100%', position:'relative' }}>
      {/* toolbar */}
      <div style={{
        position:'absolute', top:12, left:12, zIndex:10, display:'flex', gap:6,
        background:T.surface, borderRadius:8, padding:6, border:`1px solid #E5E6EB`,
        boxShadow:'0 2px 8px rgba(0,0,0,0.06)',
      }}>
        <div style={{ position:'relative' }}>
          <button onClick={() => setMenuOpen(v => !v)} style={{
            display:'flex', alignItems:'center', gap:4, padding:'6px 12px',
            borderRadius:6, cursor:'pointer', background:T.accent, color:'#fff', border:'none',
            fontSize:12, fontWeight:500, fontFamily:'inherit',
          }}>
            <Plus size={14} /> 添加节点
          </button>
          {menuOpen && (
            <div style={{
              position:'absolute', top:38, left:0, background:'#fff', borderRadius:8,
              border:'1px solid #E5E6EB', boxShadow:'0 4px 16px rgba(0,0,0,0.10)',
              overflow:'hidden', minWidth:160,
            }} onMouseLeave={() => setMenuOpen(false)}>
              {Object.entries(ADDABLE).map(([key, val]) => (
                <div key={key} onClick={() => addNode(key)} style={{
                  display:'flex', alignItems:'center', gap:8, padding:'10px 14px', cursor:'pointer',
                  fontSize:13, color:'#1D2129',
                }}>{val.icon} {val.label}</div>
              ))}
            </div>
          )}
        </div>
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding:0.3 }}
        deleteKeyCode={['Backspace', 'Delete']}
        style={{ background:'#F7F8FA' }}
      >
        <Background gap={16} size={1} color="#E5E6EB" />
        <MiniMap
          style={{ borderRadius:6, border:'1px solid #E5E6EB' }}
          nodeColor={n => {
            const t = (n.data as CustomNodeData)?.nodeType;
            if (t === 'start') return '#00B42A';
            if (t === 'end')   return '#F53F3F';
            if (t === 'condition') return '#FF7D00';
            if (t === 'router')    return '#9333EA';
            return '#3370FF';
          }}
        />
      </ReactFlow>

      {/* Node config panel */}
      {selectedNode && (
        <div style={{
          position:'absolute', top:0, right:0, width:300, height:'100%',
          background:T.surface, borderLeft:`1px solid ${T.border}`, zIndex:10,
          display:'flex', flexDirection:'column', boxShadow:'-2px 0 12px rgba(0,0,0,0.06)',
          overflow:'hidden',
        }}>
          <div style={{
            display:'flex', alignItems:'center', justifyContent:'space-between',
            padding:`${S.md}px ${S.base}px`, borderBottom:`1px solid ${T.border}`,
            flexShrink: 0,
          }}>
            <span style={{ fontSize:14, fontWeight:600, color:T.text }}>
              {selectedNode.data.label}
            </span>
            <button onClick={() => setSelectedNode(null)} style={{
              width:24, height:24, borderRadius:4, border:'none', cursor:'pointer',
              background:'transparent', color:T.secondary, display:'flex', alignItems:'center', justifyContent:'center',
            }}><X size={14} /></button>
          </div>
          <div style={{ flex: 1, padding:S.base, display:'flex', flexDirection:'column', gap:S.md, overflow:'auto', minHeight:0 }}>
            <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>节点名称</label>
            <input
              value={selectedNode.data.label}
              onChange={e => {
                const label = e.target.value;
                setNodes(nds => nds.map(n => n.id === selectedNode.id ? { ...n, data: { ...n.data, label } } : n));
                setSelectedNode(prev => prev ? { ...prev, data: { ...prev.data, label } } : null);
              }}
              style={{
                padding:'6px 10px', borderRadius:6, border:`1px solid ${T.border}`,
                fontSize:13, color:T.text, outline:'none', fontFamily:'inherit',
              }}
            />

            {selectedNode.data.nodeType === 'agent' && (
              <>
                <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>引用 Agent</label>
                <select
                  value={selectedNode.data.config?.agent_key || ''}
                  onChange={e => updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, agent_key: e.target.value })}
                  style={{
                    padding:'6px 10px', borderRadius:6, border:`1px solid ${T.border}`,
                    fontSize:13, color:T.text, outline:'none', fontFamily:'inherit', background:T.surface,
                  }}
                >
                  <option value="" disabled>选择 Agent</option>
                  {agentList.filter(a => a.agent_type === 'agent').map(a => (
                    <option key={a.key} value={a.key}>{a.name} ({a.key})</option>
                  ))}
                </select>
                {!selectedNode.data.config?.agent_key && (
                  <div style={{ fontSize:11, color:T.danger }}>请选择一个 Agent，否则无法执行</div>
                )}

                {/* 缓存设置 */}
                <CacheSettingsPanel
                  selectedNode={selectedNode}
                  agentList={agentList}
                  cacheExpanded={cacheExpanded}
                  setCacheExpanded={setCacheExpanded}
                  updateNodeConfig={updateNodeConfig}
                />
              </>
            )}

            {selectedNode.data.nodeType === 'router' && (() => {
                const selectedKey = selectedNode.data.config?.agent_key || '';
                const selectedRouter = agentList.find(a => a.key === selectedKey);
                const targets = selectedRouter?.routable || [];
                return (
              <>
                <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>路由 Agent</label>
                <select
                  value={selectedKey || 'router'}
                  onChange={e => updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, agent_key: e.target.value })}
                  style={{
                    padding:'8px 10px', borderRadius:8, border:`1px solid ${T.border}`,
                    fontSize:13, color:T.text, outline:'none', fontFamily:'inherit', background:T.surface,
                  }}
                >
                  {agentList.filter(a => a.agent_type === 'router').map(a => (
                    <option key={a.key} value={a.key}>{a.name} ({a.key})</option>
                  ))}
                </select>

                {/* 可路由目标面板 */}
                <div style={{ display:'flex', alignItems:'center', gap:7, marginTop:8 }}>
                  <GitBranch size={13} color="#9333EA" />
                  <span style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:0.4, textTransform:'uppercase' }}>可路由目标</span>
                  {targets.length > 0 && (
                    <span style={{
                      fontSize:11, fontWeight:600, color:'#9333EA', background:'#F3E8FF',
                      borderRadius:10, padding:'0 7px', lineHeight:'17px',
                    }}>{targets.length}</span>
                  )}
                </div>
                {targets.length > 0 ? (
                  <div style={{ display:'flex', flexWrap:'wrap', gap:6 }}>
                    {targets.map((t) => (
                      <span key={t} style={{
                        display:'inline-flex', alignItems:'center', gap:6,
                        padding:'4px 11px 4px 9px', borderRadius:14, fontSize:12, fontWeight:500,
                        background:T.accentBg, color:T.accent, border:'1px solid #D6E4FF', lineHeight:'18px',
                      }}>
                        <span style={{ width:5, height:5, borderRadius:'50%', background:T.accent, flexShrink:0 }} />
                        {t}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div style={{
                    padding:'10px 12px', borderRadius:8, fontSize:12, color:T.tertiary,
                    background:T.bg, border:`1px dashed ${T.border}`, lineHeight:1.5,
                  }}>
                    尚未配置可路由目标，请前往 Agent 配置页添加 routable_agents 或 L1 关键字规则
                  </div>
                )}
              </>
            )})()}

            {selectedNode.data.nodeType === 'condition' && (
              <>
                <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>判断字段 (如 node_1)</label>
                <input
                  value={selectedNode.data.config?.field || ''}
                  onChange={e => updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, field: e.target.value })}
                  placeholder="例如: node_1"
                  style={{ padding:'6px 10px', borderRadius:6, border:`1px solid ${T.border}`, fontSize:13, color:T.text, outline:'none', fontFamily:'inherit' }}
                />
                <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>操作符</label>
                <select
                  value={selectedNode.data.config?.op || 'contains'}
                  onChange={e => updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, op: e.target.value })}
                  style={{ padding:'6px 10px', borderRadius:6, border:`1px solid ${T.border}`, fontSize:13, color:T.text, outline:'none', fontFamily:'inherit', background:T.surface }}
                >
                  <option value="contains">包含 (contains)</option>
                  <option value="equals">等于 (equals)</option>
                  <option value="startsWith">开头匹配 (startsWith)</option>
                </select>
                <label style={{ fontSize:12, fontWeight:500, color:T.secondary }}>匹配值</label>
                <input
                  value={selectedNode.data.config?.value || ''}
                  onChange={e => updateNodeConfig(selectedNode.id, { ...selectedNode.data.config, value: e.target.value })}
                  placeholder="例如: 退款"
                  style={{ padding:'6px 10px', borderRadius:6, border:`1px solid ${T.border}`, fontSize:13, color:T.text, outline:'none', fontFamily:'inherit' }}
                />
                <div style={{ fontSize:11, color:T.tertiary }}>
                  条件满足时走第一条出边，不满足走最后一条（默认）
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
});

export default forwardRef(function Canvas(props: Props, ref: React.Ref<CanvasRef>) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} ref={ref} />
    </ReactFlowProvider>
  );
});
