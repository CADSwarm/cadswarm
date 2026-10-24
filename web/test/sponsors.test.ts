import { describe, expect, it } from 'vitest';
import { latestReviewOf, parseFindings, sponsorCards, toolLine } from '../src/ui/sponsors';

const base = {
  members: [
    { id: 1, name: 'you', kind: 'human', client: 'phone', role: 'member', status: 'active', statusText: '', fence: 1, online: true },
    { id: 5, name: 'reviewer', kind: 'reviewer', client: 'droid', role: 'member', status: 'left', statusText: '', fence: 1, online: false },
  ],
  objects: [{ name: 'Base' }, { name: 'Lid' }, { name: 'Params' }],
  params: [{ alias: 'wall' }],
  locks: [{ object: 'Params', holder: 1 }],
  tasks: [{ id: 't_1', status: 'open', createdBy: 5 }, { id: 't_2', status: 'done', createdBy: 5 },
    { id: 't_3', status: 'done', createdBy: 5 }, { id: 't_4', status: 'done', createdBy: 1 }],
  ops: [{ id: 'o_1', status: 'done' }, { id: 'o_2', status: 'queued' }],
  activity: [
    { id: 1, member: 1, kind: 'say', summary: 'submitted a brief', atMs: 1 },
    { id: 2, member: 5, kind: 'tool_call', summary: 'cadswarm___model_status {"detail":true}', atMs: 2 },
    { id: 3, member: 5, kind: 'tool_call', summary: 'cadswarm___create_task {"title":"Fix the wall"}', atMs: 3 },
    { id: 4, member: 5, kind: 'tool_call', summary: 'cadswarm___create_task {"title":"Lower the cutout"}', atMs: 4 },
  ],
  briefs: [{ id: 'b_2', board: 'esp32-s3-devkitc-1', status: 'ready', updatedMs: 20, planJson: '{}' }],
  reviews: [
    { id: 'r1', brief: 'b_2', round: 1, passed: false, findingsJson: '[{"object":"","problem":"the reviewer did not report a verdict"}]' },
    { id: 'r2', brief: 'b_2', round: 2, passed: false,
      findingsJson: '[{"object":"Base","problem":"the wall is 1.1 mm"},{"object":"USB","problem":"the cutout is 1.8 mm high"}]' },
  ],
} as never;

describe('sponsors panel', () => {
  it('reads a Droid tool call as the name a person would say', () => {
    expect(toolLine('cadswarm___model_status {"detail":true}')).toBe('cadswarm.model_status');
    expect(toolLine('Script {"script":"text(await tools.ToolSearch(...))"}')).toBe('Script');
    expect(toolLine('')).toBe('');
  });

  it('survives the empty or truncated findings a crashed reviewer leaves', () => {
    expect(parseFindings(undefined)).toEqual([]);
    expect(parseFindings({ findingsJson: '' })).toEqual([]);
    expect(parseFindings({ findingsJson: '[{' })).toEqual([]);
    expect(parseFindings({ findingsJson: '{"oops":1}' })).toEqual([]);
    expect(parseFindings({ findingsJson: '[{"object":"Lid","problem":"clips"}]' })).toEqual([{ object: 'Lid', problem: 'clips' }]);
  });

  it('takes the newest round, not the newest row', () => {
    expect(latestReviewOf([{ round: 3, passed: true, findingsJson: '[]' }, { round: 1, passed: false, findingsJson: '[]' }])?.round).toBe(3);
  });

  it('does not show another brief’s review as this brief’s verdict', () => {
    const state = {
      ...(base as object),
      briefs: [{ id: 'b_3', board: '', status: 'building', updatedMs: 30, planJson: '{}' }],
      reviews: [
        { id: 'r1', brief: 'b_old', round: 3, passed: true, findingsJson: '[]' },
        { id: 'r2', brief: 'b_3', round: 1, passed: false, findingsJson: '[{"object":"Lid","problem":"clips"}]' },
      ],
    } as never;
    const factory = sponsorCards(state, '').find(c => c.id === 'factory')!;
    expect(factory.facts).toContainEqual(['last review', 'round 1 · 1 findings']);
    expect(factory.list?.rows).toEqual(['Lid: clips']);
  });

  it('shows Factory as the reviewer, its verdict, its finds and the tools it called', () => {
    const factory = sponsorCards(base, 'cadswarm-demo').find(c => c.id === 'factory')!;
    expect(factory.facts).toContainEqual(['agent', 'reviewer · droid · left']);
    expect(factory.facts).toContainEqual(['last review', 'round 2 · 2 findings']);
    expect(factory.facts).toContainEqual(['tasks filed', '3']);
    expect(factory.trace).toEqual(['cadswarm.model_status', 'cadswarm.create_task', 'cadswarm.create_task']);
    expect(factory.list?.rows[0]).toContain('the wall is 1.1 mm');
  });

  it('says so when no reviewer is set up', () => {
    const factory = sponsorCards({ ...(base as object), members: [], activity: [], reviews: [] } as never, '').find(c => c.id === 'factory')!;
    expect(factory.facts).toContainEqual(['agent', 'not set up']);
    expect(factory.facts).toContainEqual(['last review', 'no review yet']);
    expect(factory.trace).toEqual([]);
  });

  it('feeds Elastic the brief, its board and the problems the Workflow distils', () => {
    const elastic = sponsorCards(base, '').find(c => c.id === 'elastic')!;
    expect(elastic.facts).toContainEqual(['board', 'esp32-s3-devkitc-1']);
    expect(elastic.facts).toContainEqual(['design memory', 'b_2 · 2 problems']);
    expect(elastic.list?.rows).toEqual(['esp32-s3-devkitc-1::the wall is 1.1 mm', 'esp32-s3-devkitc-1::the cutout is 1.8 mm high']);
  });

  it('counts the team state SpacetimeDB holds', () => {
    const space = sponsorCards(base, 'cadswarm-demo').find(c => c.id === 'spacetime')!;
    expect(space.facts).toContainEqual(['database', 'cadswarm-demo']);
    // The Params spreadsheet is not a part, and holding it is not holding a part.
    expect(space.facts).toContainEqual(['model', '2 parts · 1 params · 0 held']);
    expect(space.facts).toContainEqual(['members', '1 active · 2 joined']);
    expect(space.facts).toContainEqual(['work', '1 open tasks · 1/2 ops done']);
    expect(space.facts).toContainEqual(['events', '4 activity rows · seq 4']);
  });
});
