import BankBalance from "@/components/features/BankReconciliation/BankBalance"
import BankClearanceSummary from "@/components/features/BankReconciliation/BankClearanceSummary"
import BankPicker from "@/components/features/BankReconciliation/BankPicker"
import BankRecDateFilter from "@/components/features/BankReconciliation/BankRecDateFilter"
import BankReconciliationStatement from "@/components/features/BankReconciliation/BankReconciliationStatement"
import BankTransactions from "@/components/features/BankReconciliation/BankTransactionList"
import BankTransactionUnreconcileModal from "@/components/features/BankReconciliation/BankTransactionUnreconcileModal"
import CompanySelector from "@/components/features/BankReconciliation/CompanySelector"
import IncorrectlyClearedEntries from "@/components/features/BankReconciliation/IncorrectlyClearedEntries"
import BankTransferList from "@/components/features/BankReconciliation/BankTransferList"
import MatchAndReconcile from "@/components/features/BankReconciliation/MatchAndReconcile"
import RuleConfigureButton from "@/components/features/BankReconciliation/Rules/RuleConfigureButton"
import Settings from "@/components/features/Settings/Settings"
import ActionLog from "@/components/features/ActionLog/ActionLog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"
import { H1 } from "@/components/ui/typography"
import _ from "@/lib/translate"
import { useLayoutEffect, useRef, useState } from "react"


const BankReconciliation = () => {

    const [headerHeight, setHeaderHeight] = useState(0)

    const ref = useRef<HTMLDivElement>(null)

    useLayoutEffect(() => {
        if (ref.current) {
            setHeaderHeight(ref.current.clientHeight)
        }
    }, [])

    const remainingHeightAfterTabs = window.innerHeight - headerHeight - 324

    return (
        <div className="p-4 flex flex-col gap-4">
            <div ref={ref} className="flex flex-col gap-4">
                <div className="flex justify-between">
                    <H1 className="text-base font-medium"><span className="text-4xl font-extrabold text-emerald-500">mint</span>&nbsp; {_("Bank Reconciliation")}</H1>
                    <div className="flex items-center gap-2">
                        <TooltipProvider>
                            <RuleConfigureButton />
                            <Settings />
                            <ActionLog />
                        </TooltipProvider>
                        <CompanySelector />
                        <BankRecDateFilter />
                    </div>
                </div>
                <BankPicker />
                <BankBalance />
            </div>
            <Tabs defaultValue="Match and Reconcile">
                <TabsList className="w-full">
                    <TabsTrigger value="Match and Reconcile">{_("Conciliar")}</TabsTrigger>
                    <TabsTrigger value="Bank Statement">{_("Estados de conciliación bancarios")}</TabsTrigger>
                    <TabsTrigger value="Bank Transactions">{_("Transacciones Bancarias")}</TabsTrigger>
                    <TabsTrigger value="Bank Clearance Summary">{_("Resumen de Cambios Bancarios")}</TabsTrigger>
                    <TabsTrigger value="Incorrectly Cleared Entries">{_("Entradas Liquidadas Incorrectamente")}</TabsTrigger>
                    <TabsTrigger value="Bank Transfers">{_("Transferencias Bancarias")}</TabsTrigger>
                </TabsList>
                <TabsContent value="Match and Reconcile">
                    <MatchAndReconcile contentHeight={remainingHeightAfterTabs} />
                </TabsContent>
                <TabsContent value="Bank Statement">
                    <BankReconciliationStatement />
                </TabsContent>
                <TabsContent value="Bank Transactions">
                    <BankTransactions />
                </TabsContent>
                <TabsContent value="Bank Clearance Summary">
                    <BankClearanceSummary />
                </TabsContent>
                <TabsContent value="Incorrectly Cleared Entries">
                    <IncorrectlyClearedEntries />
                </TabsContent>
                <TabsContent value="Bank Transfers">
                    <BankTransferList />
                </TabsContent>
            </Tabs>

            <BankTransactionUnreconcileModal />
        </div>
    )
}

export default BankReconciliation