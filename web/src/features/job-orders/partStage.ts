import type { PartCondition } from '../../types';

/** Same words the client's progress emails use (api job_update_service). */
export const PART_STAGE_LABEL: Record<PartCondition, string> = {
  RAW_MATERIAL: 'Raw material',
  CLIENT_SUPPLIED_ITEM: 'Client supplied item',
  WORK_IN_PROCESS: 'Work in process',
  CUT: 'Cut',
  BLANK: 'Blank',
  FORMED: 'Formed',
  MACHINED: 'Machined',
  ASSEMBLED: 'Assembled',
  HEAT_TREATED: 'Heat treated',
  FINISHED: 'Finished',
};

/** The server moves the stage forward when an operation is completed; nobody sets it by hand. */
export const PART_STAGE_HINT =
  'Moves forward by itself when an operation is completed, e.g. welding moves it to Assembled.';

export function partStageLabel(value?: PartCondition | string | null): string | null {
  if (!value) return null;
  return PART_STAGE_LABEL[value as PartCondition] ?? value;
}
