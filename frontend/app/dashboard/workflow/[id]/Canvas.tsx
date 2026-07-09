'use client';

import { useCallback, useEffect, useImperativeHandle, forwardRef, useState } from 'react';
import {
  ReactFlow, Background, MiniMap, ReactFlowProvider,
  addEdge, useNodesState, useEdgesState,
  type Connection, type Node, type Edge,
} from '@xyflow/react';
import { Plus, User, GitBranch, Brain, X } from 'lucide-react';
import { T, S } from '@/app/theme';
import { CustomNode, type CustomNodeData } from './nodes/CustomNode';
import { CustomEdge } from './edges/CustomEdge';
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

const CanvasInner = forwardRef(function CanvasInner({ workflowId }: Props, ref: React.Ref<CanvasRef>) {
  const [nodes, setNodes, onNodesChange] = useNodesState(INITIAL_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Node config panel
  const [selectedNode, setSelectedNode] = useState<Node<CustomNodeData> | null>(null);
  const [agentList, setAgentList] = useState<{ key: string; name: string; agent_type: string; routable?: string[] }[]>([]);

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
      setAgentList((Array.isArray(list) ? list : []).map((a: any) => ({ key: a.key, name: a.name || a.key, agent_type: a.agent_type || 'agent', routable: a.routable || [] })));
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
        config: type === 'agent' ? { agent_key: agentList.find(a => a.agent_type === 'agent')?.key || '' } : type === 'router' ? { agent_key: 'router' } : undefined,
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
        }}>
          <div style={{
            display:'flex', alignItems:'center', justifyContent:'space-between',
            padding:`${S.md}px ${S.base}px`, borderBottom:`1px solid ${T.border}`,
          }}>
            <span style={{ fontSize:14, fontWeight:600, color:T.text }}>
              {selectedNode.data.label}
            </span>
            <button onClick={() => setSelectedNode(null)} style={{
              width:24, height:24, borderRadius:4, border:'none', cursor:'pointer',
              background:'transparent', color:T.secondary, display:'flex', alignItems:'center', justifyContent:'center',
            }}><X size={14} /></button>
          </div>
          <div style={{ padding:S.base, display:'flex', flexDirection:'column', gap:S.md }}>
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
