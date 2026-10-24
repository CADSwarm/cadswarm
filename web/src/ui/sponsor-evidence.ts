/**
 * A stamped snapshot of the outside services, taken from the live deployment.
 *
 * The sponsors panel shows this beside the live trace so nobody mistakes a checked number for a
 * live one. Regenerate it with:
 *
 *   python -m cadswarm.sponsor_evidence      (writes this file)
 */
export const SPONSOR_EVIDENCE = {
  checkedAt: '2026-10-24T20:35:00Z',
  elastic: { host: 'my-vectordb-project-dac20f.es.us-central1.gcp.elastic.cloud', boards: 6, designs: 1, lessons: 4 },
  factory: { droid: '0.249.0', sdk: '0.5.0' },
} as const;
