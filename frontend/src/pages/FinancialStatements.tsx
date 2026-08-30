import React, { useState, useEffect } from 'react';
import { PageContainer, PageHeader } from '../components/ui';
import { 
  Box, 
  Typography, 
  CircularProgress, 
  Alert, 
  FormControl, 
  InputLabel, 
  Select, 
  MenuItem, 
  Autocomplete,
  TextField,
  Paper
} from '@mui/material';
import { FinancialStatementsTable } from '../components/financial/FinancialStatementsTable';
import { DataCrawler } from '../components/financial/DataCrawler';
import { financialApi } from '../lib/services/financial';
import type { CompanyWithFinancialData } from '../lib/services/financial';
interface FinancialStatementData {
  company_ticker: string;
  company_name: string;
  periods: Array<{
    label: string;
    end_date: string;
    period_type: string;
  }>;
  statements: Array<{
    statement_type: string;
    title: string;
    items: Array<{
      item_id: number;
      item_key: string;
      title_vi: string;
      level: number;
      parent_item_id: number | null;
      display_order: number | null;
      values: Record<string, number | null>;
    }>;
  }>;
}

export const FinancialStatements: React.FC = () => {
  const [selectedCompany, setSelectedCompany] = useState<CompanyWithFinancialData | null>(null);
  const [selectedStatements, setSelectedStatements] = useState<string[]>(['candoiketoan', 'baocaothunhap']);
  const [financialData, setFinancialData] = useState<FinancialStatementData | null>(null);
  const [companies, setCompanies] = useState<CompanyWithFinancialData[]>([]);
  const [loading, setLoading] = useState(false);
  const [companiesLoading, setCompaniesLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const statementTypes = [
    { value: 'candoiketoan', label: 'Cân đối kế toán' },
    { value: 'baocaothunhap', label: 'Báo cáo thu nhập' },
    { value: 'luuchuyentiente', label: 'Lưu chuyển tiền tệ' },
    { value: 'thuyetminh', label: 'Thuyết minh' },
  ];

  // Fetch companies on component mount
  useEffect(() => {
    fetchCompanies();
  }, []);

  // Fetch financial data when company or statements change
  useEffect(() => {
    if (selectedCompany) {
      fetchFinancialData();
    }
  }, [selectedCompany, selectedStatements]);

  const fetchCompanies = async () => {
    setCompaniesLoading(true);
    try {
      const companiesData = await financialApi.getCompaniesWithFinancialData();
      setCompanies(companiesData);
      
      // Set the first company as default if available
      if (companiesData.length > 0 && !selectedCompany) {
        setSelectedCompany(companiesData[0]);
      }
    } catch (err) {
      console.error('Error fetching companies:', err);
      setError('Failed to load companies: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setCompaniesLoading(false);
    }
  };

  const handleDataCrawled = (symbol: string) => {
    // Refresh companies list when new data is crawled
    fetchCompanies();
    console.log(`Data crawled for ${symbol}, refreshing companies list`);
  };

  const fetchFinancialData = async () => {
    if (!selectedCompany) return;

    setLoading(true);
    setError(null);

    try {
      console.log('Fetching financial data for:', selectedCompany.ticker, 'statements:', selectedStatements);
      
      const data = await financialApi.getFinancialStatements(
        selectedCompany.ticker,
        selectedStatements,
        undefined, // periods - get all
        8, // max_periods
        5  // max_level
      );
      
      console.log('Received financial data:', data);
      setFinancialData(data);
      
      if (!data || !data.statements || data.statements.length === 0) {
        setError('No financial statements found for this company. Please ensure data has been imported.');
      }
    } catch (err) {
      console.error('Error fetching financial data:', err);
      setError(err instanceof Error ? err.message : 'An error occurred while fetching financial data');
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Financials"
        description="Báo cáo tài chính — statement data, ratios, and the crawler that keeps them current."
      />

      {/* Data Crawler Section */}
      <DataCrawler onDataCrawled={handleDataCrawled} />

      {/* Controls */}
      <Box sx={{ display: 'flex', gap: 2, mb: 3, alignItems: 'center' }}>
        <Autocomplete
          sx={{ minWidth: 350 }}
          value={selectedCompany}
          onChange={(_, newValue) => setSelectedCompany(newValue)}
          options={companies}
          getOptionLabel={(option) => `${option.ticker} - ${option.name}`}
          loading={companiesLoading}
          disabled={companiesLoading}
          isOptionEqualToValue={(option, value) => option.ticker === value.ticker}
          filterOptions={(options, { inputValue }) => {
            return options.filter(
              (option) =>
                option.ticker.toLowerCase().includes(inputValue.toLowerCase()) ||
                option.name.toLowerCase().includes(inputValue.toLowerCase())
            );
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Tìm kiếm công ty"
              placeholder="Nhập mã hoặc tên công ty..."
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {companiesLoading ? <CircularProgress color="inherit" size={20} /> : null}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
          renderOption={(props, option) => (
            <Box component="li" {...props}>
              <Box sx={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {option.ticker}
                </Typography>
              </Box>
            </Box>
          )}
          PaperComponent={({ children, ...other }) => (
            <Paper {...other} sx={{ maxHeight: 400, overflow: 'auto' }}>
              {children}
            </Paper>
          )}
          noOptionsText={
            companiesLoading ? 
              "Đang tải..." : 
              companies.length === 0 ? 
                "Không có công ty nào có dữ liệu tài chính" : 
                "Không tìm thấy công ty phù hợp"
          }
        />

        <FormControl sx={{ minWidth: 300 }}>
          <InputLabel>Loại báo cáo</InputLabel>
          <Select
            multiple
            value={selectedStatements}
            label="Loại báo cáo"
            onChange={(e) => setSelectedStatements(e.target.value as string[])}
          >
            {statementTypes.map((type) => (
              <MenuItem key={type.value} value={type.value}>
                {type.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      {/* Content */}
      {companiesLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
          <Typography variant="body1" sx={{ ml: 2 }}>
            Đang tải danh sách công ty...
          </Typography>
        </Box>
      )}

      {!companiesLoading && companies.length === 0 && !error && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Không có công ty nào có dữ liệu tài chính.
          <Box sx={{ mt: 1 }}>
            <Typography variant="body2">
              <strong>Hướng dẫn:</strong><br/>
              1. Thêm công ty: <code>python scripts/financial_data_cli.py add-company VCG "Vietcombank"</code><br/>
              2. Import dữ liệu: <code>python scripts/financial_data_cli.py import-data scripts/financial_VCG.json VCG</code>
            </Typography>
          </Box>
        </Alert>
      )}

      {loading && selectedCompany && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
          <Typography variant="body1" sx={{ ml: 2 }}>
            Đang tải dữ liệu tài chính cho {selectedCompany.ticker}...
          </Typography>
        </Box>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
          <Box sx={{ mt: 1 }}>
            <Typography variant="body2">
              <strong>Troubleshooting:</strong><br/>
              1. Ensure backend server is running: <code>uvicorn app.main:app --reload</code><br/>
              2. Check API docs: <code>http://localhost:8000/docs</code><br/>
              3. Import company data: <code>python scripts/financial_data_cli.py import-data scripts/financial_VCG.json VCG</code>
            </Typography>
          </Box>
        </Alert>
      )}

      {financialData && !loading && selectedCompany && (
        <FinancialStatementsTable data={financialData} />
      )}
    </PageContainer>
  );
};
