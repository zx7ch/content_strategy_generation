"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren
} from "react";

import { usePageData } from "@/hooks/usePageData";
import { getBrandOptions } from "@/lib/api";

interface BrandOption {
  id: string;
  name: string;
}

interface BrandContextValue {
  brands: BrandOption[];
  selectedBrandId: string | null;
  selectedBrandName: string | null;
  setSelectedBrandId: (brandId: string) => void;
  isLoading: boolean;
  loadError: unknown;
  retryBrands: () => void;
}

const BrandContext = createContext<BrandContextValue | null>(null);
export const BRAND_SELECTION_STORAGE_KEY = "xhs-growth-agent:selected-brand-id";

export function BrandProvider({ children }: PropsWithChildren) {
  const { data, error, isLoading, mutate } = usePageData("brand-options", () => getBrandOptions());
  const brands = data ?? [];
  const [selectedBrandId, setSelectedBrandIdState] = useState<string | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem(BRAND_SELECTION_STORAGE_KEY);
    if (saved) {
      setSelectedBrandIdState(saved);
    }
  }, []);

  useEffect(() => {
    if (selectedBrandId && brands.some((brand) => brand.id === selectedBrandId)) {
      return;
    }
    if (brands.length > 0) {
      const nextBrandId = brands[0].id;
      setSelectedBrandIdState(nextBrandId);
      window.localStorage.setItem(BRAND_SELECTION_STORAGE_KEY, nextBrandId);
    }
  }, [brands, selectedBrandId]);

  const value = useMemo<BrandContextValue>(() => {
    const selectedBrand = brands.find((brand) => brand.id === selectedBrandId) ?? null;
    return {
      brands,
      selectedBrandId,
      selectedBrandName: selectedBrand?.name ?? null,
      setSelectedBrandId: (brandId: string) => {
        setSelectedBrandIdState(brandId);
        window.localStorage.setItem(BRAND_SELECTION_STORAGE_KEY, brandId);
      },
      isLoading,
      loadError: error,
      retryBrands: () => {
        void mutate();
      }
    };
  }, [brands, error, isLoading, mutate, selectedBrandId]);

  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrandContext() {
  const context = useContext(BrandContext);
  if (!context) {
    throw new Error("useBrandContext must be used within BrandProvider");
  }
  return context;
}
