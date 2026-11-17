/**
 * The architecture of the product itself (BD-010 section 13.6).
 *
 * The console's surface does not show a decorative spiral of tools. It shows
 * **the architecture of GemArc**: the stages the work goes through, the tool
 * that belongs to each stage, and the flow from one stage to the next.
 *
 * The stages are a fact of the product, not a drawing decision: they say the
 * same thing as the procedure the server hands to a model
 * (`gemarc_mcp/service.py`, the `procedure` section of `gemarc_brief`).
 * Read the workflow first, then the rules, then build the model, then check
 * it, then hand it over - five stages, and the cut of the stone in a stage
 * says which stage it is.
 *
 * The rules this module keeps:
 *
 *   1. **One table.** The stage table lives here and nowhere else, so the
 *      console and the artefacts a user generates cannot disagree about what
 *      the product's stages are (section 13.4).
 *   2. **A stone is a registered tool.** The layout draws the tools the
 *      server really registered and nothing else; a tool that is not
 *      registered has no stone (section 12.3, section 12.4).
 *   3. **The cut says the stage.** The colour says read or write - a runtime
 *      fact - and the cut says the role of the stage (section 4.3).
 *   4. **The layout is pure.** It takes a tool list and returns places; it
 *      touches no three-dimensional library, so it can be checked without a
 *      canvas (section 11.5).
 *
 * @module product-architecture
 */

/**
 * The stages of the product's own workflow.
 *
 * The order is the order of the work. The cut of a stage is the cut of every
 * stone in it.
 * @type {ReadonlyArray<Readonly<{id: string, cut: string,
 *   tools: ReadonlyArray<string>}>>}
 */
export const PRODUCT_ARCHITECTURE_STAGES = Object.freeze([
  Object.freeze({
    id: 'entry',
    cut: 'wedge',
    tools: Object.freeze(['gemarc_media_doc_guide', 'gemarc_brief']),
  }),
  Object.freeze({
    id: 'rules',
    cut: 'column',
    tools: Object.freeze(['gemarc_contracts', 'gemarc_assets', 'gemarc_inspect', 'gemarc_plan']),
  }),
  Object.freeze({
    id: 'model',
    cut: 'brilliant',
    tools: Object.freeze(['gemarc_new_model', 'gemarc_scaffold', 'gemarc_init']),
  }),
  Object.freeze({
    id: 'verify',
    cut: 'shield',
    tools: Object.freeze(['gemarc_validate', 'gemarc_simulate', 'gemarc_diff', 'gemarc_verify']),
  }),
  Object.freeze({
    id: 'produce',
    cut: 'pyramid',
    tools: Object.freeze(['gemarc_render', 'gemarc_build', 'gemarc_export', 'gemarc_model']),
  }),
]);

/**
 * The cut of a tool that is not in any stage: the plain stone, the shape that
 * means nothing.
 * @type {string}
 */
export const PRODUCT_ARCHITECTURE_REST_CUT = 'plain';

/**
 * The numbers of the layout.
 *
 * The stages stand in a row along the X axis - the work flows from left to
 * right - and the tools of a stage stand next to each other, so a stage reads
 * as a cluster. Between two stages there is a gap, and the arrow of the flow
 * lives in that gap.
 * @type {Readonly<Record<string, number>>}
 */
export const PRODUCT_ARCHITECTURE_CONSTANTS = Object.freeze({
  /** The slot one tool takes in the row. */
  toolGap: 9.5,
  /** The empty gap between two stages: the arrow lives there. */
  stageGap: 11,
  /** How deep the tools of a stage are set one after the other, so the row
   * is not a flat wall. */
  zigzag: 1.2,
  /** How high the stones sit above the middle of the row. */
  lift: 0,
});

/**
 * The stage a tool belongs to.
 *
 * @param {string|null} name the tool name
 * @returns {object|null} the stage, or null when the tool is not registered
 *   in the table
 */
export function stageForTool(name) {
  if (typeof name !== 'string') return null;
  for (const stage of PRODUCT_ARCHITECTURE_STAGES) {
    if (stage.tools.indexOf(name) !== -1) return stage;
  }
  return null;
}

/**
 * The cut of a tool: the cut of its stage, or the plain stone.
 *
 * @param {string|null} name the tool name
 * @returns {string} the cut work id
 */
export function cutForTool(name) {
  const stage = stageForTool(name);
  return stage ? stage.cut : PRODUCT_ARCHITECTURE_REST_CUT;
}

/**
 * Where the stones of a tool list stand.
 *
 * The result is the same for the same tool list, every time: the stages are
 * laid out in the order of the table, and the tools of a stage in the order
 * they were registered.
 *
 * @param {Array<{name: string, write?: boolean}>} tools the registered tools
 * @param {object} [options] the options
 * @param {object} [options.constants] the numbers to override
 * @returns {{nodes: Array<object>, links: Array<object>, stages: Array<object>}}
 *   the stones, the links between the stages, and the stages that are drawn
 */
export function layoutProductArchitecture(tools, options = {}) {
  const constants = { ...PRODUCT_ARCHITECTURE_CONSTANTS, ...(options.constants || {}) };
  const list = Array.isArray(tools) ? tools : [];

  // The tools of each stage, in the order they were registered. The tools that
  // are in no stage go to the last group, as plain stones.
  const groups = PRODUCT_ARCHITECTURE_STAGES.map((stage) => ({ stage, tools: [] }));
  const rest = [];
  for (const tool of list) {
    const name = tool && tool.name;
    const stage = stageForTool(name);
    if (stage) {
      const group = groups.find((item) => item.stage.id === stage.id);
      if (group) group.tools.push(tool);
    } else if (name) {
      rest.push(tool);
    }
  }
  if (rest.length) {
    groups.push({
      stage: { id: 'rest', cut: PRODUCT_ARCHITECTURE_REST_CUT, tools: [] },
      tools: rest,
    });
  }

  // The stages that really have stones are the ones that are drawn.
  const drawn = groups.filter((group) => group.tools.length > 0);
  const nodes = [];
  const stageRecords = [];

  // The row is laid out from the left: every tool takes a slot, every stage
  // leaves a gap for the arrow of the flow.
  let cursor = 0;
  drawn.forEach((group) => {
    const count = group.tools.length;
    const width = count * constants.toolGap;
    const stageTools = [];
    group.tools.forEach((tool, index) => {
      const node = {
        name: tool.name,
        write: Boolean(tool && tool.write),
        stage: group.stage.id,
        cut: group.stage.cut,
        position: {
          x: cursor + constants.toolGap * (index + 0.5),
          y: constants.lift,
          z: (index % 2 === 0 ? -1 : 1) * constants.zigzag,
        },
      };
      nodes.push(node);
      stageTools.push(node);
    });
    stageRecords.push({
      id: group.stage.id,
      cut: group.stage.cut,
      left: cursor,
      right: cursor + width,
      nodes: stageTools,
    });
    cursor += width + constants.stageGap;
  });

  // The whole row is centred, so the picture does not lean to one side.
  const total = Math.max(cursor - constants.stageGap, 0);
  for (const node of nodes) node.position.x -= total / 2;
  for (const stage of stageRecords) {
    stage.left -= total / 2;
    stage.right -= total / 2;
  }

  // One arrow per pair of neighbouring stages that both have stones. The
  // arrow lives in the **gap** between the two stages, so it never runs
  // through a stone.
  const links = [];
  for (let index = 0; index + 1 < stageRecords.length; index += 1) {
    links.push({
      from: { x: stageRecords[index].right, y: constants.lift, z: 0 },
      to: { x: stageRecords[index + 1].left, y: constants.lift, z: 0 },
      fromStage: stageRecords[index].id,
      toStage: stageRecords[index + 1].id,
      phase: index * 0.25,
    });
  }

  return { nodes, links, stages: stageRecords };
}
