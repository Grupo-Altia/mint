import { useAtom, useSetAtom } from "jotai"
import { SelectedBank, selectedBankAccountAtom } from "./bankRecAtoms"
import { useCallback, useState } from "react"
import { useGetBankAccounts, useGetUnreconciledTransactions } from "./utils"
import { cn } from "@/lib/utils"
import { Landmark, Search } from "lucide-react"
import { H4 } from "@/components/ui/typography"
import { Input } from "@/components/ui/input"
import { getTimeago } from "@/lib/date"
import ErrorBanner from "@/components/ui/error-banner"
import _ from "@/lib/translate"

const BankPicker = ({ className, size = 'base' }: { className?: string, size?: 'base' | 'sm' }) => {

    const setSelectedBank = useSetAtom(selectedBankAccountAtom)

    const onLoadingSuccess = useCallback((data?: SelectedBank[]) => {
        if (!data) return
        if (data.length === 1) {
            setSelectedBank(data[0])
        } else if (data.length > 1) {
            const defaultBank = data.find((bank: SelectedBank) => bank.is_default)
            if (defaultBank) {
                setSelectedBank(defaultBank)
            }
        }
    }, [setSelectedBank])

    const { banks, isLoading, error } = useGetBankAccounts(onLoadingSuccess)

    const [searchQuery, setSearchQuery] = useState("")

    if (isLoading) {
        return null
    }

    if (error) {
        return <ErrorBanner error={error} />
    }

    const filteredBanks = banks?.filter((bank: SelectedBank) => 
        bank.bank?.toLowerCase().includes(searchQuery.toLowerCase()) || 
        bank.account_name?.toLowerCase().includes(searchQuery.toLowerCase()) || 
        bank.account?.toLowerCase().includes(searchQuery.toLowerCase())
    )

    return (
        <div className="flex flex-col gap-2 w-full">
            <div className="relative max-w-[300px]">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input 
                    placeholder={_("Buscar cuenta bancaria...")} 
                    value={searchQuery} 
                    onChange={(e) => setSearchQuery(e.target.value)} 
                    className="pl-8"
                />
            </div>
            <div
                className={cn("flex gap-3 items-stretch w-full overflow-x-auto bank-picker-scrollbar pr-4",
                    banks?.length > 4 ? 'pb-2' : '', className,
                )}
                style={{
                    scrollbarWidth: 'thin',
                    scrollbarColor: 'rgb(209 213 219) rgb(243 244 246)',
                }}
            >
                {
                    filteredBanks?.map((bank) => (
                        <BankPickerItem key={bank.name} bank={bank} size={size} />
                    ))
                }
            </div>
        </div>
    )
}

const BankPickerItem = ({ bank, size = 'base' }: { bank: SelectedBank, size?: 'base' | 'sm' }) => {

    const [selectedBank, setSelectedBank] = useAtom(selectedBankAccountAtom)
    const [imgError, setImgError] = useState(false)

    const isSelected = selectedBank?.name === bank.name

    const { mutate } = useGetUnreconciledTransactions()

    const onSelect = () => {
        setSelectedBank(bank)
        mutate()
    }

    return <div
        role="button"
        title={`Select ${bank.account_name}`}
        onClick={onSelect}
        className={cn('rounded-md border-2 border-border min-w-80 relative p-2 bg-card text-card-foreground overflow-hidden cursor-pointer transition-colors',
            isSelected ? 'border-primary bg-primary/10' : 'hover:bg-accent hover:text-accent-foreground',
            {
                "max-w-60 min-w-60": size === 'sm',
            }
        )}
    >
        {bank.logo && !imgError ? <img
            src={bank.logo.startsWith('/') || bank.logo.startsWith('http') ? bank.logo : `/assets/mint/mint/${bank.logo}`}
            alt={bank.bank || bank.name || ''}
            onError={() => setImgError(true)}
            className={cn("max-w-24 object-left h-10 object-contain mb-1", {
                'h-6 max-w-18 mb-2': size === 'sm',
            })}
        /> : <div className={cn("rounded-md flex items-center h-10 gap-2", {
            "h-6 mb-2": size === 'sm',
        })}>
            <Landmark size={size === 'sm' ? '16px' : '30px'} />
            <H4 className={cn("text-base mb-0", {
                'text-xs': size === 'sm',
            })}>{bank.bank}</H4>
        </div>}

        <div className="flex flex-col gap-0.5">
            <span className={cn("tracking-tight font-medium", {
                'text-xs': size === 'sm',
            })}>{bank.account_name}</span>
            <span title={_("GL Account")} className={cn("text-ellipsis line-clamp-1", size === 'sm' ? 'text-xs' : "text-sm")}>{bank.account}</span>
            {bank.last_integration_date && size !== 'sm' && <span className="text-xs text-muted-foreground">{_("Last Synced Transaction")}: {getTimeago(bank.last_integration_date)}</span>}
        </div>

        <div className={cn("absolute -top-1 right-0", {
            "-top-1.5": size === 'sm',
        })}>
            {bank.account_type && <span className={cn("uppercase rounded-bl-sm text-xs tracking-tight font-semibold py-1 px-1.5",
                isSelected ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground',
                {
                    'text-[10px]': size === 'sm',
                }
            )}>
                {bank.account_type?.slice(0, 24)}
            </span>}
        </div>

    </div >
}

export default BankPicker