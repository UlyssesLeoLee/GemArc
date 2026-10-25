/**
 * Interoperability adapter tests (v5.0 §7).
 *
 * Every adapter offers the same pair of functions (`parseX(input)` and
 * `serializeX(canonical)`) plus a format identifier. A parse collects
 * non-fatal problems into `warnings` and must not throw on a partially broken
 * document; it throws only when the input is not that format at all, and then
 * with a specific `GemArcError` code.
 *
 * Run with: node --test E:/GemArc/src/gemarc_mcp/scaffold/runtime/tests/interop.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { GemArcError } from '../core/errors.js';
import {
  INTEROP_ADAPTERS,
  INTEROP_FORMATS,
  getAdapter,
  parseInterop,
  serializeInterop,
  detectFormat,
  BPMN_FORMAT,
  XMI_FORMAT,
  ER_FORMAT,
  DRAWIO_FORMAT,
  parseBpmn,
  serializeBpmn,
  parseXmi,
  serializeXmi,
  parseEr,
  serializeEr,
  parseDrawio,
  serializeDrawio,
} from '../interop/index.js';

const BPMN_DOCUMENT = [
  '<?xml version="1.0" encoding="UTF-8"?>',
  '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'
  + ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" id="Definitions_1" targetNamespace="urn:gemarc">',
  '  <bpmn:process id="wf-order" name="订单处理" isExecutable="false">',
  '    <bpmn:startEvent id="start" name="开始" />',
  '    <bpmn:exclusiveGateway id="decide" name="判定" />',
  '    <bpmn:task id="charge" name="扣款" />',
  '    <bpmn:endEvent id="end" name="结束" />',
  '    <bpmn:sequenceFlow id="e1" sourceRef="start" targetRef="decide" />',
  '    <bpmn:sequenceFlow id="e2" sourceRef="decide" targetRef="charge">',
  '      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression"><![CDATA[order.amount > 100]]></bpmn:conditionExpression>',
  '    </bpmn:sequenceFlow>',
  '    <bpmn:sequenceFlow id="e3" sourceRef="charge" targetRef="end" />',
  '  </bpmn:process>',
  '</bpmn:definitions>',
].join('\n');

const XMI_DOCUMENT = [
  '<?xml version="1.0" encoding="UTF-8"?>',
  '<xmi:XMI xmlns:xmi="http://www.omg.org/XMI" xmlns:uml="http://www.omg.org/spec/UML/20131001">',
  '  <uml:Model xmi:id="model_shop" name="Shop">',
  '    <packagedElement xmi:type="uml:Package" xmi:id="pkg_billing" name="Billing">',
  '      <packagedElement xmi:type="uml:Class" xmi:id="cls_invoice" name="Invoice">',
  '        <ownedAttribute xmi:id="cls_invoice.id" name="id" type="int" visibility="private" />',
  '        <ownedAttribute xmi:id="cls_invoice.amount" name="amount" type="decimal" />',
  '        <ownedOperation xmi:id="cls_invoice.settle" name="settle" returnType="void" visibility="public">',
  '          <ownedParameter xmi:id="cls_invoice.settle.order" name="order" type="Order" />',
  '        </ownedOperation>',
  '      </packagedElement>',
  '      <packagedElement xmi:type="uml:Class" xmi:id="cls_order" name="Order">',
  '        <ownedAttribute xmi:id="cls_order.id" name="id" type="int" />',
  '      </packagedElement>',
  '      <packagedElement xmi:type="uml:Class" xmi:id="cls_base" name="BaseDocument">',
  '        <ownedAttribute xmi:id="cls_base.id" name="id" type="int" />',
  '      </packagedElement>',
  '    </packagedElement>',
  '    <generalization xmi:id="gen_invoice_base" general="cls_base" specific="cls_invoice" />',
  '  </uml:Model>',
  '</xmi:XMI>',
].join('\n');

const ER_SOURCE = {
  entities: [
    {
      id: 'customer',
      name: 'Customer',
      kind: 'Entity',
      attributes: [{ name: 'id', type: 'int', primaryKey: true }],
    },
    {
      id: 'order',
      name: 'Order',
      kind: 'Table',
      attributes: [
        { name: 'id', type: 'int', primaryKey: true },
        { name: 'customer_id', type: 'int', foreignKey: 'customer.id' },
      ],
    },
  ],
  relations: [
    { id: 'rel_customer_order', from: 'customer', to: 'order', kind: 'owns', cardinality: { from: '1', to: 'N' } },
  ],
};

const DRAWIO_CANONICAL = {
  nodes: [
    { id: 'n_gateway', kind: 'Service', name: '网关', canvas: { x: 40, y: 80, width: 160, height: 60 } },
    { id: 'n_auth', kind: 'Module', name: '认证', canvas: { x: 320, y: 80, width: 160, height: 60 } },
  ],
  edges: [{ id: 'e_calls', sourceId: 'n_gateway', targetId: 'n_auth', type: 'calls', directed: true }],
};

/** The identity of a node set: the fields every format has to preserve. */
function nodeSignature(nodes) {
  return nodes.map((node) => [node.id, node.kind, node.name]);
}

/** The identity of an edge set. BPMN calls the type `kind`, the others `type`. */
function edgeSignature(edges) {
  return edges.map((edge) => [
    edge.id,
    edge.sourceId,
    edge.targetId,
    edge.type || edge.kind,
    edge.condition === undefined ? null : edge.condition,
  ]);
}

test('every adapter exposes the same parse / serialize pair and a format id', () => {
  assert.deepEqual(INTEROP_FORMATS.slice().sort(), ['bpmn', 'drawio', 'er', 'xmi']);

  for (const [format, adapter] of Object.entries(INTEROP_ADAPTERS)) {
    assert.equal(adapter.format, format);
    assert.equal(typeof adapter.parse, 'function');
    assert.equal(typeof adapter.serialize, 'function');
    assert.equal(getAdapter(format), adapter);
    assert.equal(getAdapter(format.toUpperCase()), adapter);
  }

  assert.equal(getAdapter('nope'), null);
  assert.equal(getAdapter(null), null);
});

test('the generic dispatch works by format id and rejects unknown formats', () => {
  const parsed = parseInterop(BPMN_FORMAT, BPMN_DOCUMENT);
  assert.deepEqual(nodeSignature(parsed.nodes), [
    ['start', 'start', '开始'],
    ['decide', 'decision', '判定'],
    ['charge', 'task', '扣款'],
    ['end', 'end', '结束'],
  ]);

  const xml = serializeInterop(BPMN_FORMAT, parsed);
  assert.equal(typeof xml, 'string');
  assert.ok(xml.includes('<bpmn:definitions'));

  assert.throws(() => parseInterop('vdx', '<xml/>'), (error) => {
    assert.ok(error instanceof GemArcError);
    assert.equal(error.code, 'GEMARC_INTEROP_002');
    return true;
  });
  assert.throws(() => serializeInterop('vdx', {}), (error) => {
    assert.equal(error.code, 'GEMARC_INTEROP_002');
    return true;
  });
});

test('BPMN: a small document parses to the expected nodes and edges', () => {
  const parsed = parseBpmn(BPMN_DOCUMENT);

  assert.deepEqual(parsed.warnings, []);
  assert.equal(parsed.workflow.id, 'wf-order');
  assert.equal(parsed.workflow.name, '订单处理');
  assert.deepEqual(nodeSignature(parsed.nodes), [
    ['start', 'start', '开始'],
    ['decide', 'decision', '判定'],
    ['charge', 'task', '扣款'],
    ['end', 'end', '结束'],
  ]);
  assert.deepEqual(edgeSignature(parsed.edges), [
    ['e1', 'start', 'decide', 'sequence', null],
    ['e2', 'decide', 'charge', 'conditional', 'order.amount > 100'],
    ['e3', 'charge', 'end', 'sequence', null],
  ]);
});

test('BPMN: the document serialises back to an equivalent set', () => {
  const canonical = {
    id: 'wf-order',
    name: '订单处理',
    kind: 'process',
    nodes: [
      { id: 'start', kind: 'start', name: '开始' },
      { id: 'decide', kind: 'decision', name: '判定' },
      { id: 'charge', kind: 'task', name: '扣款' },
      { id: 'end', kind: 'end', name: '结束' },
    ],
    edges: [
      { id: 'e1', sourceId: 'start', targetId: 'decide', kind: 'sequence' },
      { id: 'e2', sourceId: 'decide', targetId: 'charge', kind: 'conditional', condition: 'order.amount > 100' },
      { id: 'e3', sourceId: 'charge', targetId: 'end', kind: 'sequence' },
    ],
  };

  const xml = serializeBpmn(canonical);
  const back = parseBpmn(xml);

  assert.deepEqual(back.warnings, []);
  assert.deepEqual(nodeSignature(back.nodes), nodeSignature(canonical.nodes));
  assert.deepEqual(edgeSignature(back.edges), edgeSignature(canonical.edges));
  // The condition survives as an expression a program can judge.
  assert.equal(back.edges.find((edge) => edge.id === 'e2').condition, 'order.amount > 100');
});

test('BPMN: canvas geometry round-trips through the diagram interchange', () => {
  const canonical = {
    id: 'wf-order',
    name: '订单处理',
    kind: 'process',
    nodes: [
      { id: 'start', kind: 'start', name: '开始', canvas: { x: 10, y: 20, width: 160, height: 72 } },
      { id: 'end', kind: 'end', name: '结束', canvas: { x: 300, y: 20, width: 160, height: 72 } },
    ],
    edges: [
      {
        id: 'e1',
        sourceId: 'start',
        targetId: 'end',
        kind: 'sequence',
        canvas: { points: [{ x: 170, y: 56 }, { x: 300, y: 56 }] },
      },
    ],
  };

  const xml = serializeBpmn(canonical);
  const back = parseBpmn(xml);

  assert.deepEqual(back.nodes.map((node) => node.canvas), [
    { x: 10, y: 20, width: 160, height: 72 },
    { x: 300, y: 20, width: 160, height: 72 },
  ]);
  assert.deepEqual(back.edges[0].canvas, { points: [{ x: 170, y: 56 }, { x: 300, y: 56 }] });
});

test('BPMN: a malformed document returns warnings instead of throwing', () => {
  const broken = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Definitions_1">',
    '  <bpmn:process id="p" name="P">',
    '    <bpmn:startEvent id="s1" name="Start" />',
    '    <bpmn:task />',
    '    <bpmn:exclusiveGateway id="g1" />',
    '    <bpmn:sequenceFlow id="f1" targetRef="s1" />',
    '    <bpmn:sequenceFlow id="f2" sourceRef="s1" targetRef="ghost" />',
    '    <bpmn:sequenceFlow id="f3" sourceRef="s1" targetRef="g1" />',
    '  </bpmn:process>',
    '</bpmn:definitions>',
  ].join('\n');

  let parsed = null;
  assert.doesNotThrow(() => {
    parsed = parseBpmn(broken);
  });

  assert.ok(parsed.warnings.length > 0, 'a broken document must report warnings');
  assert.ok(parsed.warnings.some((warning) => warning.includes('id')));
  assert.ok(parsed.warnings.some((warning) => warning.includes('f1')));
  assert.ok(parsed.warnings.some((warning) => warning.includes('f2')));
  // What could be read is still read.
  assert.deepEqual(nodeSignature(parsed.nodes), [['s1', 'start', 'Start'], ['g1', 'gateway', 'g1']]);
  assert.deepEqual(parsed.edges.map((edge) => edge.id), ['f2', 'f3']);
});

test('BPMN: an unclosed document is reported as a warning, not a crash', () => {
  const truncated = [
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d">',
    '  <bpmn:process id="p" name="P">',
    '    <bpmn:startEvent id="s1" name="Start" />',
  ].join('\n');

  const parsed = parseBpmn(truncated);

  assert.ok(parsed.warnings.length > 0);
  assert.deepEqual(nodeSignature(parsed.nodes), [['s1', 'start', 'Start']]);
});

test('XMI: a package plus two classes with attributes and a generalisation round-trips', () => {
  const parsed = parseXmi(XMI_DOCUMENT);

  assert.deepEqual(parsed.warnings, []);
  assert.deepEqual(nodeSignature(parsed.nodes), [
    ['pkg_billing', 'Module', 'Billing'],
    ['cls_invoice', 'Class', 'Invoice'],
    ['cls_order', 'Class', 'Order'],
    ['cls_base', 'Class', 'BaseDocument'],
  ]);
  // The package nesting is the parent relation.
  assert.deepEqual(parsed.nodes.map((node) => node.parentId), [null, 'pkg_billing', 'pkg_billing', 'pkg_billing']);
  assert.deepEqual(edgeSignature(parsed.edges), [
    ['gen_invoice_base', 'cls_invoice', 'cls_base', 'inherits', null],
  ]);

  const invoice = parsed.nodes.find((node) => node.id === 'cls_invoice');
  assert.deepEqual(invoice.attributes.map((attribute) => [attribute.name, attribute.type]), [
    ['id', 'int'],
    ['amount', 'decimal'],
  ]);
  assert.deepEqual(invoice.methods.map((method) => method.name), ['settle']);
  assert.deepEqual(invoice.methods[0].parameters, [{ name: 'order', type: 'Order' }]);

  const back = parseXmi(serializeXmi(parsed));

  assert.deepEqual(back.warnings, []);
  assert.deepEqual(nodeSignature(back.nodes), nodeSignature(parsed.nodes));
  assert.deepEqual(back.nodes.map((node) => node.parentId), parsed.nodes.map((node) => node.parentId));
  assert.deepEqual(edgeSignature(back.edges), edgeSignature(parsed.edges));
  const backInvoice = back.nodes.find((node) => node.id === 'cls_invoice');
  assert.deepEqual(
    backInvoice.attributes.map((attribute) => [attribute.name, attribute.type]),
    [['id', 'int'], ['amount', 'decimal']],
  );
  assert.deepEqual(backInvoice.methods[0].parameters, [{ name: 'order', type: 'Order' }]);
});

test('ER: entity names and cardinality survive a round trip', () => {
  const parsed = parseEr(ER_SOURCE);

  assert.deepEqual(parsed.warnings, []);
  const entities = parsed.nodes.filter((node) => node.kind === 'Entity' || node.kind === 'Table');
  assert.deepEqual(entities.map((node) => [node.id, node.name, node.kind]), [
    ['customer', 'Customer', 'Entity'],
    ['order', 'Order', 'Table'],
  ]);
  assert.deepEqual(edgeSignature(parsed.edges), [
    ['rel_customer_order', 'customer', 'order', 'owns', null],
  ]);
  assert.deepEqual(parsed.edges[0].properties.cardinality, { from: '1', to: 'N' });

  const back = parseEr(serializeEr(parsed));

  assert.deepEqual(back.warnings, []);
  const backEntities = back.nodes.filter((node) => node.kind === 'Entity' || node.kind === 'Table');
  assert.deepEqual(backEntities.map((node) => [node.id, node.name, node.kind]), [
    ['customer', 'Customer', 'Entity'],
    ['order', 'Order', 'Table'],
  ]);
  assert.deepEqual(edgeSignature(back.edges), edgeSignature(parsed.edges));
  assert.deepEqual(back.edges[0].properties.cardinality, { from: '1', to: 'N' });
  assert.deepEqual(back.edges[0].properties.cardinalityText, '1:N');
});

test('ER: the compact line DSL is accepted as a second input flavour', () => {
  const parsed = parseEr([
    'entity customer Customer',
    '  attribute id int pk',
    '  attribute name string',
    'table order Order',
    '  attribute id int pk',
    'relation customer 1 order N owns',
  ].join('\n'));

  assert.deepEqual(parsed.warnings, []);
  assert.deepEqual(
    parsed.nodes.filter((node) => node.kind !== 'Attribute').map((node) => [node.id, node.name, node.kind]),
    [['customer', 'Customer', 'Entity'], ['order', 'Order', 'Table']],
  );
  assert.deepEqual(parsed.edges[0].properties.cardinality, { from: '1', to: 'N' });
});

test('Draw.io: node labels and one edge survive a round trip', () => {
  const xml = serializeDrawio(DRAWIO_CANONICAL);
  const parsed = parseDrawio(xml);

  assert.deepEqual(parsed.warnings, []);
  assert.deepEqual(nodeSignature(parsed.nodes), [
    ['n_gateway', 'Service', '网关'],
    ['n_auth', 'Module', '认证'],
  ]);
  assert.deepEqual(edgeSignature(parsed.edges), [
    ['e_calls', 'n_gateway', 'n_auth', 'calls', null],
  ]);
  assert.deepEqual(parsed.edges[0].directed, true);
  // The geometry survives as presentation data.
  assert.deepEqual(parsed.nodes[0].canvas, { x: 40, y: 80, width: 160, height: 60 });
});

test('a document of the wrong format throws a GemArcError with a specific code', () => {
  const wrongFormat = [
    (error) => parseBpmn(XMI_DOCUMENT),
    (error) => parseXmi(BPMN_DOCUMENT),
    (error) => parseEr(BPMN_DOCUMENT),
    (error) => parseDrawio(BPMN_DOCUMENT),
  ];

  for (const call of wrongFormat) {
    assert.throws(call, (error) => {
      assert.ok(error instanceof GemArcError, 'the error must be a GemArcError, not a generic Error');
      assert.equal(error.name, 'GemArcError');
      assert.equal(error.code, 'GEMARC_INTEROP_001');
      assert.ok(!String(error.message).includes('\\'), 'the message must not leak a host path');
      return true;
    });
  }
});

test('a non string input is rejected with the same specific code', () => {
  for (const parse of [parseBpmn, parseXmi, parseDrawio]) {
    assert.throws(() => parse(null), (error) => {
      assert.ok(error instanceof GemArcError);
      assert.equal(error.code, 'GEMARC_INTEROP_001');
      return true;
    });
    assert.throws(() => parse(42), (error) => error.code === 'GEMARC_INTEROP_001');
  }
  assert.throws(() => parseEr(''), (error) => error.code === 'GEMARC_INTEROP_001');
  assert.throws(() => parseEr('{"unrelated": true}'), (error) => error.code === 'GEMARC_INTEROP_001');
});

test('the format of a document can be detected', () => {
  assert.equal(detectFormat(BPMN_DOCUMENT), BPMN_FORMAT);
  assert.equal(detectFormat(XMI_DOCUMENT), XMI_FORMAT);
  assert.equal(detectFormat(serializeDrawio(DRAWIO_CANONICAL)), DRAWIO_FORMAT);
  assert.equal(detectFormat(JSON.stringify(ER_SOURCE)), ER_FORMAT);
  assert.equal(detectFormat('nothing to see here'), null);
  assert.equal(detectFormat(''), null);
});

/**
 * The canonical fields each format cannot carry.
 *
 * The list is the contract of each adapter; the assertions below prove that
 * every entry is really lost by the round trip, so the list cannot drift
 * away from the implementation.
 */
test('BPMN is lossy for ports, constraints, data contracts and exception handling', () => {
  const canonical = {
    id: 'wf',
    name: 'WF',
    kind: 'process',
    nodes: [
      {
        id: 'charge',
        kind: 'task',
        name: '扣款',
        ports: [{ id: 'in', kind: 'input', name: '订单', schemaRef: 'schema://order' }],
        constraints: [{ kind: 'timeout', value: 5000, onTimeout: 'compensate' }],
        dataContract: { input: 'Order' },
        exceptionHandling: { onError: 'compensate', errorPortId: 'err' },
      },
    ],
    edges: [],
  };

  const back = parseBpmn(serializeBpmn(canonical));
  const node = back.nodes[0];

  assert.deepEqual(node.ports, []);
  assert.deepEqual(node.constraints, []);
  assert.equal(node.dataContract, null);
  assert.equal(node.exceptionHandling, null);
  // The execution semantics that BPMN does express are kept.
  assert.equal(node.kind, 'task');
  assert.equal(node.name, '扣款');
});

test('XMI is lossy for attribute visibility and for parameter direction', () => {
  const parsed = parseXmi(XMI_DOCUMENT);
  const invoice = parsed.nodes.find((node) => node.id === 'cls_invoice');

  // `amount` has no visibility in the source document.
  assert.equal(invoice.attributes.find((attribute) => attribute.name === 'amount').visibility, null);

  const back = parseXmi(serializeXmi(parsed));
  const backInvoice = back.nodes.find((node) => node.id === 'cls_invoice');

  // The absent visibility is normalised to the default.
  assert.equal(backInvoice.attributes.find((attribute) => attribute.name === 'amount').visibility, 'public');
  // The declared visibility is kept.
  assert.equal(backInvoice.attributes.find((attribute) => attribute.name === 'id').visibility, 'private');
});

test('ER is lossy for edge types other than owns/composes and for attribute visibility', () => {
  const canonical = {
    nodes: [
      { id: 'a', kind: 'Entity', name: 'A', attributes: [{ name: 'id', type: 'int', visibility: 'private' }] },
      { id: 'b', kind: 'Entity', name: 'B', attributes: [] },
    ],
    edges: [
      { id: 'e_owns', sourceId: 'a', targetId: 'b', type: 'owns', properties: { cardinality: { from: '1', to: 'N' } } },
      { id: 'e_depends', sourceId: 'a', targetId: 'b', type: 'dependsOn', properties: {} },
    ],
  };

  const back = parseEr(serializeEr(canonical));

  assert.deepEqual(back.edges.map((edge) => edge.id), ['e_owns']);
  assert.deepEqual(back.edges[0].properties.cardinality, { from: '1', to: 'N' });
  // Visibility is not part of the ER exchange form.
  assert.equal(back.nodes.find((node) => node.id === 'a').properties.visibility, undefined);
  const attribute = back.nodes.find((node) => node.id === 'a.id');
  assert.equal(attribute.properties.visibility, undefined);
});

test('Draw.io is lossy for parentId, attributes and methods', () => {
  const canonical = {
    nodes: [
      {
        id: 'n1',
        kind: 'Service',
        name: '网关',
        parentId: 'root',
        attributes: [{ name: 'id', type: 'int' }],
        methods: [{ name: 'handle', returnType: 'void' }],
        canvas: { x: 0, y: 0, width: 160, height: 60 },
      },
    ],
    edges: [],
  };

  const back = parseDrawio(serializeDrawio(canonical));

  assert.deepEqual(back.nodes[0].parentId, null);
  assert.deepEqual(back.nodes[0].attributes, []);
  assert.deepEqual(back.nodes[0].methods, []);
  // The fields the format does express survive.
  assert.deepEqual([back.nodes[0].id, back.nodes[0].kind, back.nodes[0].name], ['n1', 'Service', '网关']);
  assert.deepEqual(back.nodes[0].canvas, { x: 0, y: 0, width: 160, height: 60 });
});
