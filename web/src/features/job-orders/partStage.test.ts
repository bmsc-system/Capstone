import { describe, expect, it } from 'vitest';
import { partStageLabel } from './partStage';

describe('partStageLabel', () => {
  it('names the stage the server recorded', () => {
    expect(partStageLabel('ASSEMBLED')).toBe('Assembled');
    expect(partStageLabel('CLIENT_SUPPLIED_ITEM')).toBe('Client supplied item');
  });

  it('shows nothing when the job has no stage yet', () => {
    expect(partStageLabel(null)).toBeNull();
    expect(partStageLabel(undefined)).toBeNull();
  });
});
