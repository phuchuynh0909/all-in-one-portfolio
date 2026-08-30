import { Alert, Box, CircularProgress, Typography } from '@mui/material';
import { useCatalog } from '../lib/experiments/catalog';

export default function Experiments() {
  const { data, isLoading, error } = useCatalog();

  if (isLoading) return <CircularProgress />;
  if (error) return <Alert severity="error">{(error as Error).message}</Alert>;
  if (!data?.runs.length) {
    return (
      <Alert severity="info">
        No experiments yet. Run <code>log_experiment(pf, name=...)</code> in a notebook,
        or repair a missing catalog with <code>ExperimentStore.from_env().rebuild_catalog()</code>.
      </Alert>
    );
  }

  return (
    <Box>
      <Typography variant="h5">Experiments</Typography>
      <Typography variant="body2">{data.runs.length} runs</Typography>
    </Box>
  );
}
