
import React, { useState, useEffect } from 'react';
import { ProductData, SimulationResult } from '../types';
import { formatCurrency } from '../services/pricingEngine';
import { getPricingInsights } from '../services/geminiService';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, PieChart, Pie } from 'recharts';

interface PricingDashboardProps {
  product: ProductData;
  result: SimulationResult | null;
  suggestedPrice: number;
  userEmail: string;
  uf: string;
}

const PricingDashboard: React.FC<PricingDashboardProps> = ({ product, result, suggestedPrice, userEmail, uf }) => {
  const [aiInsight, setAiInsight] = useState<string>('Analisando estratégia...');
  const [isLogging, setIsLogging] = useState(false);

  useEffect(() => {
    if (result) {
      setAiInsight('Solicitando insight estratégico...');
      getPricingInsights(product, result, suggestedPrice).then(setAiInsight);
    }
  }, [product, result, suggestedPrice]);

  if (!result) return null;

  const chartData = [
    { name: 'Custo Base', value: product.Custo },
    { name: 'Variáveis', value: result.custosVariaveis - product.Custo },
    { name: 'Custos Fixos', value: product.Custo_Fixo },
    { name: 'Margem EBITDA', value: Math.max(0, result.margemEbitda) }
  ];

  const handleLog = () => {
    setIsLogging(true);
    // Simulate Supabase Insert
    setTimeout(() => {
      setIsLogging(false);
      alert('Simulação registrada com sucesso no log operacional.');
    }, 600);
  };

  return (
    <div className="space-y-8 animate-fadeIn">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Simulação de Preço</h1>
          <p className="text-slate-500">Resultados projetados para {product.SKU} em {uf}</p>
        </div>
        <button 
          onClick={handleLog}
          disabled={isLogging}
          className="bg-green-600 hover:bg-green-700 text-white font-semibold py-2 px-6 rounded-lg shadow-sm transition flex items-center gap-2 self-start"
        >
          {isLogging ? (
            <span className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></span>
          ) : (
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg>
          )}
          Registrar Simulação
        </button>
      </header>

      {/* Primary Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Receita Líquida Estimada</p>
          <p className="text-2xl font-bold text-slate-900 mt-1">{formatCurrency(result.receitaLiquida)}</p>
          <div className="mt-2 text-xs text-slate-400">Pós-impostos ({product.Impostos}%)</div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Margem de Contribuição</p>
          <p className="text-2xl font-bold text-slate-900 mt-1">{formatCurrency(result.margemContribuicao)}</p>
          <div className="mt-2 text-xs text-slate-400">Total Variável: {formatCurrency(result.custosVariaveis)}</div>
        </div>

        <div className={`p-6 rounded-xl shadow-sm border transition duration-300 ${result.status === 'profit' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
          <div className="flex justify-between items-start">
            <p className="text-sm font-medium text-slate-600">Margem EBITDA Final</p>
            <span className="text-xl">{result.status === 'profit' ? '🟢' : '🔴'}</span>
          </div>
          <p className={`text-2xl font-bold mt-1 ${result.status === 'profit' ? 'text-green-700' : 'text-red-700'}`}>
            {formatCurrency(result.margemEbitda)}
          </p>
          <div className="mt-2 text-xs opacity-70">
            Lucratividade: {((result.margemEbitda / suggestedPrice) * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Composition Chart */}
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h3 className="text-lg font-bold text-slate-800 mb-6">Composição do Preço</h3>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="name" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(val) => `R$${val}`} />
                <Tooltip 
                  contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                  formatter={(val: number) => [formatCurrency(val), 'Valor']}
                />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={index === 3 ? (result.status === 'profit' ? '#10b981' : '#ef4444') : '#3b82f6'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* AI Insights Card */}
        <div className="bg-blue-900 text-white p-6 rounded-xl shadow-lg relative overflow-hidden flex flex-col">
          <div className="absolute top-0 right-0 p-4 opacity-10">
            <svg className="w-24 h-24" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg>
          </div>
          <div className="relative z-10 flex flex-col h-full">
            <h3 className="text-lg font-bold flex items-center gap-2 mb-4">
              <svg className="w-5 h-5 text-blue-300" fill="currentColor" viewBox="0 0 20 20"><path d="M11 3a1 1 0 10-2 0v1a1 1 0 102 0V3zM15.657 5.757a1 1 0 00-1.414-1.414l-.707.707a1 1 0 001.414 1.414l.707-.707zM18 10a1 1 0 01-1 1h-1a1 1 0 110-2h1a1 1 0 011 1zM5.05 6.464A1 1 0 106.464 5.05l-.707-.707a1 1 0 00-1.414 1.414l.707.707zM5 10a1 1 0 01-1 1H3a1 1 0 110-2h1a1 1 0 011 1zM8 16v-1a1 1 0 112 0v1a1 1 0 11-2 0zM13.536 14.95a1 1 0 011.414 1.414l-.707.707a1 1 0 01-1.414-1.414l.707-.707zM6.464 14.95a1 1 0 010 1.414l-.707.707a1 1 0 01-1.414-1.414l.707-.707z"></path></svg>
              Insight IA Gemini
            </h3>
            <p className="text-blue-100 text-lg italic leading-relaxed flex-1">
              "{aiInsight}"
            </p>
            <div className="mt-6 pt-6 border-t border-blue-800 text-xs text-blue-300">
              Análise baseada em parâmetros de mercado projetados para 2026.
            </div>
          </div>
        </div>
      </div>

      {/* Detailed Breakdown Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-4 border-b border-slate-200 bg-slate-50">
          <h3 className="font-bold text-slate-800">Detalhamento Financeiro (Unitário)</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-xs uppercase text-slate-500 bg-slate-50 font-bold">
              <tr>
                <th className="px-6 py-3">Componente</th>
                <th className="px-6 py-3">Valor Nominal</th>
                <th className="px-6 py-3">% S/ Preço Bruto</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-sm">
              <tr>
                <td className="px-6 py-4 font-medium text-slate-900">Preço Bruto</td>
                <td className="px-6 py-4">{formatCurrency(suggestedPrice)}</td>
                <td className="px-6 py-4">100.0%</td>
              </tr>
              <tr>
                <td className="px-6 py-4 font-medium text-slate-900 text-red-600">Impostos</td>
                <td className="px-6 py-4">{formatCurrency(suggestedPrice * (product.Impostos / 100))}</td>
                <td className="px-6 py-4">{product.Impostos.toFixed(1)}%</td>
              </tr>
              <tr>
                <td className="px-6 py-4 font-medium text-slate-900">Custo Mercadoria</td>
                <td className="px-6 py-4">{formatCurrency(product.Custo)}</td>
                <td className="px-6 py-4">{((product.Custo / suggestedPrice) * 100).toFixed(1)}%</td>
              </tr>
              <tr>
                <td className="px-6 py-4 font-medium text-slate-900">Logística / Frete</td>
                <td className="px-6 py-4">{formatCurrency(product.Frete)}</td>
                <td className="px-6 py-4">{((product.Frete / suggestedPrice) * 100).toFixed(1)}%</td>
              </tr>
              <tr className="bg-slate-50 font-bold">
                <td className="px-6 py-4 text-blue-800">Margem EBITDA</td>
                <td className="px-6 py-4 text-blue-800">{formatCurrency(result.margemEbitda)}</td>
                <td className="px-6 py-4 text-blue-800">{((result.margemEbitda / suggestedPrice) * 100).toFixed(1)}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default PricingDashboard;