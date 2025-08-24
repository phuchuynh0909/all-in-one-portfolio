import React from 'react';
import { Container, Typography, Box } from '@mui/material';
import { ReportTable } from '../components/report/ReportTable';
import { fetchReports } from '../lib/services/report';
import type { Report as ReportType } from '../lib/services/report';

const Report: React.FC = () => {
  const [reports, setReports] = React.useState<ReportType[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);

  const loadReports = async (symbol?: string) => {
    try {
      setIsLoading(true);
      const data = await fetchReports(symbol);
      console.log(data);
      setReports(data);
    } catch (error) {
      console.error('Error loading reports:', error);
    } finally {
      setIsLoading(false);
    }
  };

  React.useEffect(() => {
    loadReports();
  }, []);

  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 3 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          Research Reports
        </Typography>
        <ReportTable
          reports={reports}
          isLoading={isLoading}
          onSymbolSearch={(symbol) => loadReports(symbol)}
        />
      </Box>
    </Container>
  );
};

export default Report;