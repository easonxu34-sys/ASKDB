import test from "node:test";
import assert from "node:assert/strict";
import {
  getThreadGroupKey,
  getThreadGroupPageState,
  getThreadGroupRenderState,
} from "../lib/thread-grouping.mjs";

test("data-source identity keeps groups distinct even when names match", () => {
  assert.notEqual(
    getThreadGroupKey("source-a", "运营数据"),
    getThreadGroupKey("source-b", "运营数据"),
  );
});

test("a group heading appears only at the start of each adjacent group", () => {
  const sourceA = getThreadGroupKey("source-a", "订单分析库");
  const sourceB = getThreadGroupKey("source-b", "物流运营库");

  assert.deepEqual(getThreadGroupRenderState(sourceA, undefined, new Set()), {
    showHeading: true,
    collapsed: false,
    renderThread: true,
  });
  assert.equal(getThreadGroupRenderState(sourceA, sourceA, new Set()).showHeading, false);
  assert.equal(getThreadGroupRenderState(sourceB, sourceA, new Set()).showHeading, true);
});

test("collapsed groups keep their heading and hide each child thread", () => {
  const source = getThreadGroupKey("source-a", "订单分析库");
  const collapsed = new Set([source]);

  assert.deepEqual(getThreadGroupRenderState(source, undefined, collapsed), {
    showHeading: true,
    collapsed: true,
    renderThread: false,
  });
  assert.deepEqual(getThreadGroupRenderState(source, source, collapsed), {
    showHeading: false,
    collapsed: true,
    renderThread: false,
  });
});

test("group headings are marked from ordered server data and continue across pages", () => {
  const pageOne = getThreadGroupPageState([
    { data_source_id: "source-a", data_source_name: "订单库" },
    { data_source_id: "source-a", data_source_name: "订单库" },
  ]);
  const pageTwo = getThreadGroupPageState(
    [
      { data_source_id: "source-a", data_source_name: "订单库" },
      { data_source_id: "source-b", data_source_name: "订单库" },
    ],
    pageOne.at(-1)?.groupKey,
  );

  assert.deepEqual(
    pageOne.map(({ showHeading, showDivider }) => ({ showHeading, showDivider })),
    [
      { showHeading: true, showDivider: false },
      { showHeading: false, showDivider: false },
    ],
  );
  assert.deepEqual(
    pageTwo.map(({ showHeading, showDivider }) => ({ showHeading, showDivider })),
    [
      { showHeading: false, showDivider: false },
      { showHeading: true, showDivider: true },
    ],
  );
});
