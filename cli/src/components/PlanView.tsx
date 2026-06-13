import { Box, Text } from "ink";
import { TaskStatusBadge } from "./TaskStatusBadge.js";
import type { SubtaskInfo } from "../types.js";

interface Props {
  tasks: SubtaskInfo[];
}

export function PlanView({ tasks }: Props) {
  if (!tasks || tasks.length === 0) return null;

  const levels = buildLevels(tasks);

  return (
    <Box
      flexDirection="column"
      padding={1}
      borderStyle="round"
      borderColor="cyan"
    >
      <Text bold color="cyan">
        Execution Plan
      </Text>
      {levels.map((level, i) => (
        <Box key={i} flexDirection="column" marginLeft={i * 2}>
          {level.map((task) => (
            <Box key={task.id}>
              <Text dimColor>{task.id}: </Text>
              <Text>{task.description}</Text>
              <Text> </Text>
              <TaskStatusBadge status={task.status} />
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  );
}

/**
 * Build a visual tree from the DAG using topological depth.
 * Level 0 = root tasks (no dependencies), Level N = tasks at depth N.
 */
function buildLevels(tasks: SubtaskInfo[]): SubtaskInfo[][] {
  const taskMap = new Map(tasks.map((t) => [t.id, t]));

  // Topological depth via memoized DFS
  const depth = new Map<string, number>();
  function getDepth(id: string): number {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;

    const task = taskMap.get(id);
    if (!task || task.depends_on.length === 0) {
      depth.set(id, 0);
      return 0;
    }
    const d = 1 + Math.max(...task.depends_on.map((dep) => getDepth(dep)));
    depth.set(id, d);
    return d;
  }

  for (const t of tasks) getDepth(t.id);

  // Group by depth
  const levels: SubtaskInfo[][] = [];
  for (const [id, d] of depth) {
    const task = taskMap.get(id)!;
    while (levels.length <= d) levels.push([]);
    levels[d].push(task);
  }
  return levels;
}
